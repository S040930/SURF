"""Framework-neutral r21 domain service shared by REST and MCP.

No FastAPI or MCP type appears here.  Both transports receive the same domain
errors and operate on the same SQLAlchemy transaction.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.r20.dataset import build_frozen_dataset
from app.experiment.r21 import PROTOCOL_ID
from app.experiment.r21.codex_runner import CodexExecRunner, RunnerUnavailableError
from app.experiment.r21.memory import parse_memory_update
from app.experiment.r21.protocol import (
    TRAJECTORIES,
    ProjectKind,
    PromptTemplates,
    RunnerConfigIn,
    expected_question_calls,
    output_schema,
    schedule_for_kind,
)
from app.models.r21 import (
    R21Call,
    R21CallAttempt,
    R21Exposure,
    R21Project,
    R21PromptVersion,
    R21Record,
    R21Report,
    R21RunGroup,
    R21RunnerConfig,
    R21RunnerRuntime,
    R21Stream,
)

RUNNER_HEARTBEAT_SECONDS = 10
RUNNER_STALE_SECONDS = 30


class R21DomainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _analysis_code_sha() -> str:
    from app.experiment.r20 import analysis

    return hashlib.sha256(inspect.getsource(analysis).encode()).hexdigest()


def _normalize_templates(value: PromptTemplates) -> dict[str, str]:
    return {
        key: text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        for key, text in value.model_dump(mode="json").items()
    }


def _prompt_hash(templates: dict[str, str]) -> str:
    return canonical_sha256({"protocol": PROTOCOL_ID, "templates": templates})


class R21Service:
    def __init__(self, db: Session, runner_factory=CodexExecRunner):
        self.db = db
        self.runner_factory = runner_factory

    def runtime_status(self) -> dict[str, Any]:
        queued = int(
            self.db.scalar(
                select(func.count())
                .select_from(R21RunGroup)
                .join(R21Project)
                .where(
                    R21Project.status == "running",
                    R21RunGroup.status.in_(("queued", "running")),
                )
            )
            or 0
        )
        row = self.db.get(R21RunnerRuntime, 1)
        now = utc_now_naive()
        online = bool(
            row
            and row.worker_id
            and row.heartbeat_at
            and row.heartbeat_at >= now - timedelta(seconds=RUNNER_STALE_SECONDS)
        )
        active = self.db.scalar(
            select(R21RunGroup)
            .join(R21Project)
            .where(
                R21Project.status.in_(("running", "attention_required")),
                R21RunGroup.status.in_(("queued", "running", "blocked")),
            )
            .order_by(R21RunGroup.started_at, R21RunGroup.order_rank)
            .limit(1)
        )
        reason = None
        if not online:
            reason = (
                "MCP runner heartbeat expired"
                if row and row.worker_id and row.heartbeat_at
                else "no MCP runner is connected"
            )
        return {
            "runner_online": online,
            "queued_groups": queued,
            "worker_id": row.worker_id if online and row else None,
            "connected_at": row.connected_at if row else None,
            "last_heartbeat_at": row.heartbeat_at if row else None,
            "last_seen_at": row.last_seen_at if row else None,
            "runtime": dict(row.runtime_json) if row else {},
            "active_group": self.group_out(active) if active else None,
            "heartbeat_seconds": RUNNER_HEARTBEAT_SECONDS,
            "stale_after_seconds": RUNNER_STALE_SECONDS,
            "reason": reason,
        }

    def acquire_runner_lease(self, worker_id: str, runtime: dict[str, Any]) -> bool:
        """Claim the singleton worker lease if it is free or stale."""
        now = utc_now_naive()
        row = self.db.scalar(
            select(R21RunnerRuntime).where(R21RunnerRuntime.id == 1).with_for_update()
        )
        if row is None:
            row = R21RunnerRuntime(id=1, runtime_json={})
            self.db.add(row)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                row = self.db.scalar(
                    select(R21RunnerRuntime)
                    .where(R21RunnerRuntime.id == 1)
                    .with_for_update()
                )
                if row is None:
                    raise
        stale = not row.heartbeat_at or row.heartbeat_at < now - timedelta(
            seconds=RUNNER_STALE_SECONDS
        )
        if row.worker_id not in (None, worker_id) and not stale:
            self.db.rollback()
            return False
        previous_owner = row.worker_id
        if previous_owner and previous_owner != worker_id:
            self._recover_worker_leases(previous_owner)
        if row.worker_id != worker_id:
            row.connected_at = now
        row.worker_id = worker_id
        row.heartbeat_at = now
        row.last_seen_at = now
        row.runtime_json = dict(runtime)
        self.db.commit()
        return True

    def heartbeat_runner_lease(self, worker_id: str) -> bool:
        row = self.db.scalar(
            select(R21RunnerRuntime).where(R21RunnerRuntime.id == 1).with_for_update()
        )
        if row is None or row.worker_id != worker_id:
            self.db.rollback()
            return False
        now = utc_now_naive()
        row.heartbeat_at = now
        row.last_seen_at = now
        self.db.commit()
        return True

    def release_runner_lease(self, worker_id: str) -> bool:
        row = self.db.scalar(
            select(R21RunnerRuntime).where(R21RunnerRuntime.id == 1).with_for_update()
        )
        if row is None or row.worker_id != worker_id:
            self.db.rollback()
            return False
        self._recover_worker_leases(worker_id)
        row.worker_id = None
        row.last_seen_at = utc_now_naive()
        self.db.commit()
        return True

    def _recover_worker_leases(self, worker_id: str) -> None:
        streams = list(
            self.db.scalars(
                select(R21Stream).where(
                    R21Stream.worker_id == worker_id,
                    R21Stream.status == "leased",
                )
            )
        )
        for stream in streams:
            pending = self.db.scalar(
                select(R21Call)
                .where(
                    R21Call.project_id == stream.project_id,
                    R21Call.question_id == stream.question_id,
                    R21Call.condition == stream.condition,
                    R21Call.trajectory == stream.trajectory,
                    R21Call.status == "pending",
                )
                .order_by(R21Call.id.desc())
                .limit(1)
            )
            if pending:
                pending.status = "retry_pending"
                pending.failure_reason = "MCP runner disconnected during execution"
            stream.status = "pending"
            stream.worker_id = None
            stream.lease_until = None

        # r23 calls are intentionally not retried silently.  If an MCP host dies
        # after claiming a call, surface the orphaned lease for manual review and
        # preserve the interrupted attempt in the audit trail.
        from app.experiment.r23.executor import LEASE_SECONDS as R23_LEASE_SECONDS
        from app.models.r23 import (
            R23CallAttempt,
            R23Event,
            R23ModelBinding,
            R23Project,
            R23RunGroup,
            R23UniqueEvaluation,
        )

        evaluations = list(
            self.db.scalars(
                select(R23UniqueEvaluation).where(
                    R23UniqueEvaluation.worker_id == worker_id,
                    R23UniqueEvaluation.status == "leased",
                )
            )
        )
        for evaluation in evaluations:
            project = self.db.get(R23Project, evaluation.project_id)
            group = self.db.get(R23RunGroup, evaluation.run_group_id)
            binding = self.db.get(R23ModelBinding, evaluation.model_binding_id)
            if project is None or group is None or binding is None:
                raise RuntimeError(
                    "r23 leased call references missing snapshot records"
                )

            existing_attempt = self.db.scalar(
                select(R23CallAttempt.id).where(
                    R23CallAttempt.evaluation_id == evaluation.id,
                    R23CallAttempt.attempt_number == evaluation.attempt_count,
                )
            )
            if existing_attempt is None:
                runtime = binding.frozen_runtime_json
                started_at = (
                    evaluation.lease_until - timedelta(seconds=R23_LEASE_SECONDS)
                    if evaluation.lease_until
                    else utc_now_naive()
                )
                self.db.add(
                    R23CallAttempt(
                        project_id=project.id,
                        evaluation_id=evaluation.id,
                        attempt_number=evaluation.attempt_count,
                        input_sha256=evaluation.input_sha256,
                        requested_model=binding.model,
                        reasoning_effort=str(runtime["reasoning_effort"]),
                        speed_mode=str(runtime["speed_mode"]),
                        timeout_seconds=int(runtime["timeout_seconds"]),
                        status="failed",
                        latency_ms=0,
                        error_code="interrupted",
                        error_summary="MCP connection ended during the call",
                        started_at=started_at,
                    )
                )

            evaluation.status = "attention_required"
            evaluation.failure_code = "interrupted"
            evaluation.failure_summary = "MCP connection ended during the call"
            evaluation.worker_id = None
            evaluation.lease_until = None
            group.status = "blocked"
            project.status = "attention_required"
            self.db.add(
                R23Event(
                    project_id=project.id,
                    event_type="worker_interruption_detected",
                    detail_json={
                        "call_id": evaluation.id,
                        "input_sha256": evaluation.input_sha256,
                        "previous_worker_id": worker_id,
                    },
                )
            )

    # Runner configuration -------------------------------------------------
    def list_runner_configs(self) -> list[R21RunnerConfig]:
        return list(
            self.db.scalars(
                select(R21RunnerConfig).order_by(R21RunnerConfig.created_at.desc())
            )
        )

    def create_runner_config(self, payload: dict[str, Any]) -> R21RunnerConfig:
        config = RunnerConfigIn.model_validate(payload).model_dump(mode="json")
        row = R21RunnerConfig(
            id=str(uuid.uuid4()),
            name=config.pop("name").strip(),
            config_json=config,
            config_sha256=canonical_sha256(config),
            status="draft",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def update_runner_config(
        self, config_id: str, payload: dict[str, Any]
    ) -> R21RunnerConfig:
        row = self._runner(config_id)
        self._require(
            row.status == "draft",
            "conflict",
            "frozen runner configuration cannot change",
        )
        config = RunnerConfigIn.model_validate(payload).model_dump(mode="json")
        row.name, row.config_json = config.pop("name").strip(), config
        row.config_sha256 = canonical_sha256(config)
        self.db.commit()
        return row

    def clone_runner_config(self, config_id: str) -> R21RunnerConfig:
        source = self._runner(config_id)
        row = R21RunnerConfig(
            id=str(uuid.uuid4()),
            name=f"Copy of {source.name}",
            config_json=dict(source.config_json),
            config_sha256=source.config_sha256,
            status="draft",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def freeze_runner_config(self, config_id: str) -> R21RunnerConfig:
        row = self._runner(config_id)
        self._require(
            row.status == "draft", "conflict", "only draft runners can be frozen"
        )
        try:
            row.frozen_runtime_json = self.runner_factory().frozen_runtime(
                row.config_json
            )
        except RunnerUnavailableError as exc:
            raise R21DomainError("runner_unavailable", str(exc)) from exc
        row.status, row.frozen_at = "frozen", utc_now_naive()
        self.db.commit()
        return row

    def delete_runner_config(self, config_id: str) -> None:
        row = self._runner(config_id)
        used = self.db.scalar(
            select(R21Project.id).where(R21Project.runner_config_id == row.id)
        )
        self._require(
            used is None, "conflict", "runner configuration is used by a project"
        )
        self.db.delete(row)
        self.db.commit()

    # Prompts ---------------------------------------------------------------
    def list_prompt_versions(self) -> list[R21PromptVersion]:
        return list(
            self.db.scalars(
                select(R21PromptVersion).order_by(R21PromptVersion.created_at.desc())
            )
        )

    def create_prompt_version(
        self, *, name: str, templates: dict[str, str]
    ) -> R21PromptVersion:
        normalized = _normalize_templates(PromptTemplates.model_validate(templates))
        row = R21PromptVersion(
            id=str(uuid.uuid4()),
            protocol_id=PROTOCOL_ID,
            name=name.strip(),
            templates_json=normalized,
            templates_sha256=_prompt_hash(normalized),
            status="draft",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def freeze_prompt_version(self, version_id: str) -> R21PromptVersion:
        row = self._prompt(version_id)
        self._require(
            row.status == "draft",
            "conflict",
            "only draft prompt versions can be frozen",
        )
        templates = PromptTemplates.model_validate(row.templates_json)
        # These checks are deliberately deterministic: no hidden LLM validation suite.
        self._require(
            _prompt_hash(_normalize_templates(templates)) == row.templates_sha256,
            "validation",
            "prompt text hash changed",
        )
        for kind, condition in (
            ("test_score", None),
            ("memory_update", "crm"),
            ("memory_update", "arm"),
        ):
            output_schema(kind, condition).model_json_schema()
        # Exercise the fixed local validators with invalid values to ensure they remain importable.
        try:
            parse_memory_update(
                "crm", {"rules": []}, "answer", "question words", "reference words"
            )
        except ValueError:
            pass
        row.status, row.frozen_at = "frozen", utc_now_naive()
        self.db.commit()
        return row

    def delete_prompt_version(self, version_id: str) -> None:
        row = self._prompt(version_id)
        used = self.db.scalar(
            select(R21Project.id).where(R21Project.prompt_version_id == row.id)
        )
        self._require(used is None, "conflict", "prompt version is used by a project")
        self.db.delete(row)
        self.db.commit()

    # Projects --------------------------------------------------------------
    def list_projects(self) -> list[dict[str, Any]]:
        runtime = self.runtime_status()
        return [
            self.project_out(row, runtime=runtime, include_performance=False)
            for row in self.db.scalars(
                select(R21Project).order_by(R21Project.created_at.desc())
            )
        ]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.project_out(self._project(project_id))

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name, kind = str(payload["name"]).strip(), ProjectKind(payload["kind"]).value
        runner, prompt = self._runner(str(payload["runner_config_id"])), self._prompt(
            str(payload["prompt_version_id"])
        )
        self._require(
            runner.status == "frozen" and runner.frozen_runtime_json,
            "conflict",
            "runner must be frozen",
        )
        self._require(
            prompt.status == "frozen", "conflict", "prompt version must be frozen"
        )
        pilot_id = payload.get("pilot_project_id")
        if kind == "formal":
            self._require(
                bool(pilot_id),
                "validation",
                "formal projects require a completed technical pilot",
            )
            pilot = self._project(str(pilot_id))
            self._require(
                pilot.kind == "pilot_run" and pilot.status == "completed",
                "conflict",
                "pilot must be completed",
            )
            self._require(
                pilot.runner_config_sha256 == runner.config_sha256
                and pilot.prompt_version_sha256 == prompt.templates_sha256,
                "conflict",
                "formal project must exactly match its technical pilot",
            )
        signature = (
            canonical_sha256(
                {
                    "protocol": PROTOCOL_ID,
                    "runner": runner.config_sha256,
                    "prompt": prompt.templates_sha256,
                }
            )
            if kind == "formal"
            else None
        )
        row = R21Project(
            id=str(uuid.uuid4()),
            protocol_id=PROTOCOL_ID,
            name=name,
            kind=kind,
            status="draft",
            runner_config_id=runner.id,
            runner_config_json=dict(runner.config_json),
            runner_config_sha256=runner.config_sha256,
            runner_runtime_json=dict(runner.frozen_runtime_json),
            prompt_version_id=prompt.id,
            prompt_version_name=prompt.name,
            prompt_templates_json=dict(prompt.templates_json),
            prompt_version_sha256=prompt.templates_sha256,
            pilot_project_id=pilot_id,
            formal_signature=signature,
            manifest_json={"protocol": PROTOCOL_ID},
            manifest_sha256="0" * 64,
            data_sha256="0" * 64,
            analysis_code_sha256=_analysis_code_sha(),
        )
        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise R21DomainError(
                "conflict",
                "a formal project with this frozen configuration already exists",
            ) from exc
        return self.project_out(row)

    def freeze_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status == "draft", "conflict", "only draft projects can be frozen"
        )
        dataset = build_frozen_dataset(
            settings.R20_SAF_ARCHIVE_PATH, settings.R20_SAF_SPLIT_MAP_PATH
        )
        if project.kind == "formal":
            pilot = self._project(str(project.pilot_project_id))
            self._require(
                pilot.status == "completed"
                and pilot.data_sha256 == dataset.manifest["manifest_sha256"]
                and pilot.analysis_code_sha256 == project.analysis_code_sha256
                and pilot.runner_runtime_json.get("prompt_envelope_version")
                == project.runner_runtime_json.get("prompt_envelope_version"),
                "conflict",
                "formal project must exactly match completed pilot data, analysis, and CLI envelope",
            )
        role = "formal" if project.kind == "formal" else "pilot_run"
        questions = sorted(key for key, value in dataset.roles.items() if value == role)
        schedule, expected, order = schedule_for_kind(project.kind), 0, {}
        available = {
            row.answer_id: row for row in dataset.records if row.excluded_reason is None
        }
        for question_index, question in enumerate(questions):
            tests = {
                row.answer_id: row
                for row in dataset.records_for(question, "test_unseen_answers")
            }
            endpoints = [
                tests[answer_id] for answer_id in dataset.test_endpoints[question]
            ]
            expected += expected_question_calls(
                schedule.memory_count, len(endpoints), len(schedule.probe_checkpoints)
            )
            for position, row in enumerate(endpoints):
                self._add_record(
                    project,
                    row,
                    question,
                    dataset.max_scores[question],
                    "test",
                    0,
                    position,
                    row.answer_id in dataset.probes[question],
                )
            for trajectory, ids in dataset.trajectories[question].items():
                for position, answer_id in enumerate(ids, 1):
                    self._add_record(
                        project,
                        available[answer_id],
                        question,
                        dataset.max_scores[question],
                        "memory",
                        int(trajectory),
                        position,
                        False,
                    )
            group_sequence = []
            for condition_index, condition in enumerate(("nm", "crm", "arm")):
                group_expected = (
                    3 * len(dataset.probes[question]) * len(schedule.probe_checkpoints)
                    + 6 * len(endpoints)
                    + (3 * schedule.memory_count if condition != "nm" else 0)
                )
                self.db.add(
                    R21RunGroup(
                        project_id=project.id,
                        question_id=question,
                        condition=condition,
                        status="pending",
                        order_rank=question_index * 3 + condition_index,
                        expected_calls=group_expected,
                    )
                )
                for trajectory in TRAJECTORIES:
                    rank = condition_index * 3 + trajectory - 1
                    self.db.add(
                        R21Stream(
                            project_id=project.id,
                            question_id=question,
                            condition=condition,
                            trajectory=trajectory,
                            order_rank=rank,
                            status="pending",
                        )
                    )
                    group_sequence.append(
                        {"condition": condition, "trajectory": trajectory}
                    )
            order[question] = group_sequence
        manifest = {
            **dataset.manifest,
            "protocol": PROTOCOL_ID,
            "selected_questions": questions,
            "schedule": asdict(schedule),
            "expected_calls": expected,
            "runner_config_sha256": project.runner_config_sha256,
            "prompt_version_sha256": project.prompt_version_sha256,
            "analysis_code_sha256": project.analysis_code_sha256,
            "prompt_envelope_version": project.runner_runtime_json[
                "prompt_envelope_version"
            ],
            "call_order": order,
        }
        project.manifest_json, project.manifest_sha256 = manifest, canonical_sha256(
            manifest
        )
        project.data_sha256, project.status, project.frozen_at = (
            dataset.manifest["manifest_sha256"],
            "frozen",
            utc_now_naive(),
        )
        self.db.commit()
        return self.project_out(project)

    def start_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status == "frozen",
            "conflict",
            "only a frozen project can start",
        )
        self._require_no_other_running_project(project.id)
        current = build_frozen_dataset(
            settings.R20_SAF_ARCHIVE_PATH, settings.R20_SAF_SPLIT_MAP_PATH
        )
        self._require(
            current.manifest["manifest_sha256"] == project.data_sha256
            and _analysis_code_sha() == project.analysis_code_sha256,
            "conflict",
            "frozen data or analysis code hash differs from the project manifest",
        )
        group = self.db.scalar(
            select(R21RunGroup)
            .where(
                R21RunGroup.project_id == project.id,
                R21RunGroup.status == "pending",
            )
            .order_by(R21RunGroup.order_rank)
            .limit(1)
        )
        if group is None:
            raise R21DomainError("conflict", "project has no pending run groups")
        group.status, group.started_at = "queued", utc_now_naive()
        project.status = "running"
        project.started_at = project.started_at or utc_now_naive()
        self.db.add(
            R21Exposure(
                project_id=project.id,
                question_id=None,
                action="start_project",
                detail_json={"first_group_id": group.id},
            )
        )
        self.db.commit()
        return self.project_out(project)

    def pause_project(self, project_id: str) -> dict[str, Any]:
        row = self._project(project_id)
        self._require(
            row.status == "running", "conflict", "only running projects can pause"
        )
        row.status = "paused"
        self.db.add(
            R21Exposure(
                project_id=row.id, question_id=None, action="pause", detail_json={}
            )
        )
        self.db.commit()
        return self.project_out(row)

    def resume_project(self, project_id: str) -> dict[str, Any]:
        row = self._project(project_id)
        self._require(
            row.status == "paused", "conflict", "only paused projects can resume"
        )
        self._require_no_other_running_project(row.id)
        active_group = self.db.scalar(
            select(R21RunGroup.id).where(
                R21RunGroup.project_id == row.id,
                R21RunGroup.status.in_(("queued", "running")),
            )
        )
        if active_group is None:
            next_group = self.db.scalar(
                select(R21RunGroup)
                .where(
                    R21RunGroup.project_id == row.id,
                    R21RunGroup.status == "pending",
                )
                .order_by(R21RunGroup.order_rank)
                .limit(1)
            )
            self._require(
                next_group is not None, "conflict", "project has no work to resume"
            )
            next_group.status = "queued"
            next_group.started_at = utc_now_naive()
        row.status = "running"
        self.db.add(
            R21Exposure(
                project_id=row.id, question_id=None, action="resume", detail_json={}
            )
        )
        self.db.commit()
        return self.project_out(row)

    def terminate_project(self, project_id: str) -> dict[str, Any]:
        row = self._project(project_id)
        self._require(
            row.status
            in {"draft", "frozen", "running", "paused", "attention_required"},
            "conflict",
            "project cannot terminate",
        )
        row.status, row.terminated_at = "terminated", utc_now_naive()
        self.db.commit()
        return self.project_out(row)

    def delete_project(self, project_id: str) -> None:
        row = self._project(project_id)
        self._require(
            row.status not in {"running"},
            "conflict",
            "pause or terminate project before deletion",
        )
        self.db.delete(row)
        self.db.commit()

    def list_groups(self, project_id: str) -> list[dict[str, Any]]:
        self._project(project_id)
        return [
            self.group_out(row)
            for row in self.db.scalars(
                select(R21RunGroup)
                .where(R21RunGroup.project_id == project_id)
                .order_by(R21RunGroup.order_rank)
            )
        ]

    def list_calls(
        self, project_id: str, *, failures_only: bool = False, limit: int = 200
    ) -> dict[str, Any]:
        project = self._project(project_id)
        query = select(R21Call).where(R21Call.project_id == project.id)
        if failures_only:
            query = query.where(
                R21Call.status.in_(("failed_terminal", "retry_pending"))
            )
        rows = list(self.db.scalars(query.order_by(R21Call.id.desc()).limit(limit)))
        return {
            "items": [self.call_out(row) for row in rows],
            "total": len(rows),
            "summary": self.progress(project),
        }

    def retry_call(self, project_id: str, call_id: int) -> dict[str, Any]:
        project, call = self._project(project_id), self.db.get(R21Call, call_id)
        self._require(
            call is not None and call.project_id == project.id,
            "not_found",
            "call does not exist",
        )
        self._require(
            call.status == "failed_terminal",
            "conflict",
            "only terminal failures may retry",
        )
        self._require(
            project.status == "attention_required",
            "conflict",
            "project is not waiting for failure recovery",
        )
        self._require_no_other_running_project(project.id)
        stream = self.db.scalar(
            select(R21Stream).where(
                R21Stream.project_id == project.id,
                R21Stream.question_id == call.question_id,
                R21Stream.condition == call.condition,
                R21Stream.trajectory == call.trajectory,
            )
        )
        group = self.db.scalar(
            select(R21RunGroup).where(
                R21RunGroup.project_id == project.id,
                R21RunGroup.question_id == call.question_id,
                R21RunGroup.condition == call.condition,
            )
        )
        self._require(
            stream is not None and group is not None,
            "conflict",
            "failed execution state is incomplete",
        )
        call.status, call.failure_reason, call.retry_count = "retry_pending", None, 0
        stream.status, stream.worker_id, stream.lease_until = "pending", None, None
        group.status = "running"
        project.status = "running"
        self.db.add(
            R21Exposure(
                project_id=project.id,
                question_id=call.question_id,
                action="retry_and_continue",
                detail_json={"call_id": call.id},
            )
        )
        self.db.commit()
        return self.project_out(project)

    def get_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        report = self.db.get(R21Report, project.id)
        return {
            "project_id": project.id,
            "status": project.status,
            "report": report.report_json if report else None,
            "report_sha256": report.report_sha256 if report else None,
        }

    # Serialization / helpers ---------------------------------------------
    def progress(self, project: R21Project) -> dict[str, Any]:
        counts = dict(
            self.db.execute(
                select(R21Call.status, func.count())
                .where(R21Call.project_id == project.id)
                .group_by(R21Call.status)
            ).all()
        )
        return {
            "total": int(project.manifest_json.get("expected_calls", 0)),
            "recorded": sum(counts.values()),
            **counts,
        }

    def performance(self, project: R21Project) -> dict[str, Any]:
        latencies = sorted(
            int(value)
            for value in self.db.scalars(
                select(R21CallAttempt.latency_ms)
                .join(R21Call, R21CallAttempt.call_id == R21Call.id)
                .where(
                    R21Call.project_id == project.id,
                    R21CallAttempt.status == "succeeded",
                )
            )
        )
        progress = self.progress(project)
        succeeded = int(progress.get("succeeded", 0))
        total = int(progress.get("total", 0))
        remaining = max(total - succeeded, 0)
        average_ms = (sum(latencies) / len(latencies)) if latencies else None
        end = project.completed_at or utc_now_naive()
        elapsed_seconds = (
            max((end - project.started_at).total_seconds(), 0.001)
            if project.started_at
            else None
        )

        def percentile(fraction: float) -> int | None:
            if not latencies:
                return None
            index = round((len(latencies) - 1) * fraction)
            return latencies[index]

        return {
            "successful_attempts": len(latencies),
            "throughput_calls_per_hour": (
                round(succeeded * 3600 / elapsed_seconds, 2)
                if elapsed_seconds
                else None
            ),
            "average_latency_ms": round(average_ms) if average_ms else None,
            "p50_latency_ms": percentile(0.50),
            "p95_latency_ms": percentile(0.95),
            "estimated_remaining_seconds": (
                round(remaining * average_ms / 1000) if average_ms else None
            ),
        }

    def project_out(
        self,
        row: R21Project,
        *,
        runtime: dict[str, Any] | None = None,
        include_performance: bool = True,
    ) -> dict[str, Any]:
        status = row.status
        runtime = runtime or self.runtime_status()
        return {
            "id": row.id,
            "protocol_id": row.protocol_id,
            "name": row.name,
            "kind": row.kind,
            "status": (
                "waiting_for_runner"
                if status == "running" and not runtime["runner_online"]
                else status
            ),
            "runner_config_id": row.runner_config_id,
            "runner_config_json": row.runner_config_json,
            "prompt_version_id": row.prompt_version_id,
            "prompt_version_name": row.prompt_version_name,
            "pilot_project_id": row.pilot_project_id,
            "manifest_json": (
                row.manifest_json
                if status not in {"running", "paused", "attention_required"}
                else {"protocol": row.protocol_id, "blinded": True}
            ),
            "progress": self.progress(row),
            "performance": self.performance(row) if include_performance else {},
            "runtime": runtime,
            "created_at": row.created_at,
            "frozen_at": row.frozen_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    def group_out(self, row: R21RunGroup) -> dict[str, Any]:
        counts = dict(
            self.db.execute(
                select(R21Call.status, func.count())
                .where(
                    R21Call.project_id == row.project_id,
                    R21Call.question_id == row.question_id,
                    R21Call.condition == row.condition,
                )
                .group_by(R21Call.status)
            ).all()
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "question_id": row.question_id,
            "condition": row.condition,
            "status": row.status,
            "order_rank": row.order_rank,
            "expected_calls": row.expected_calls,
            "progress": {"recorded": sum(counts.values()), **counts},
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    @staticmethod
    def call_out(row: R21Call) -> dict[str, Any]:
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "question_id": row.question_id,
            "condition": row.condition,
            "trajectory": row.trajectory,
            "history_count": row.history_count,
            "repeat": row.repeat,
            "output_json": row.output_json,
            "failure_reason": row.failure_reason,
        }

    def _runner(self, config_id: str) -> R21RunnerConfig:
        row = self.db.get(R21RunnerConfig, config_id)
        if row is None:
            raise R21DomainError("not_found", "runner configuration does not exist")
        return row

    def _prompt(self, version_id: str) -> R21PromptVersion:
        row = self.db.get(R21PromptVersion, version_id)
        if row is None:
            raise R21DomainError("not_found", "prompt version does not exist")
        return row

    def _project(self, project_id: str) -> R21Project:
        row = self.db.get(R21Project, project_id)
        if row is None:
            raise R21DomainError("not_found", "r21 project does not exist")
        return row

    def _require_no_other_running_project(self, project_id: str) -> None:
        other = self.db.scalar(
            select(R21Project.id).where(
                R21Project.id != project_id,
                R21Project.status == "running",
            )
        )
        self._require(
            other is None,
            "conflict",
            "another r21 project is already running",
        )

    @staticmethod
    def _require(condition: bool, code: str, message: str) -> None:
        if not condition:
            raise R21DomainError(code, message)

    def _add_record(
        self, project, row, question, max_score, usage, trajectory, position, probe
    ) -> None:
        self.db.add(
            R21Record(
                project_id=project.id,
                answer_id=row.answer_id,
                group_id=row.group_id,
                question_id=question,
                question_text=row.question_text,
                reference_answer=row.reference_answer,
                student_answer=row.student_answer,
                teacher_score=row.teacher_score,
                teacher_feedback=row.teacher_feedback,
                max_score=max_score,
                source_split=row.source_split,
                usage=usage,
                trajectory=trajectory,
                position=position,
                probe=probe,
            )
        )
