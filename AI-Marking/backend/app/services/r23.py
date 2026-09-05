"""Framework-neutral r23 lifecycle, audit, export, and report service."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.r21.codex_runner import CodexExecRunner, RunnerUnavailableError
from app.experiment.r23 import PROTOCOL_ID
from app.experiment.r23.dataset import (
    DataGateError,
    audit_data_root,
    materialize_sample,
    public_manifest,
)
from app.experiment.r23.protocol import (
    ProjectKind,
    RubricIn,
    RunnerConfigIn,
    canonical_protocol_manifest,
)
from app.models.r23 import (
    R23CallAttempt,
    R23EvaluationObservation,
    R23Event,
    R23LockedReport,
    R23ModelBinding,
    R23Project,
    R23RubricVersion,
    R23RunGroup,
    R23RunnerConfig,
    R23SampleObservation,
    R23UniqueEvaluation,
)


class R23DomainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _safe_error(code: str) -> str:
    return {
        "timeout": "Codex 调用超时；未自动重试。",
        "runner_drift": "开始实验时记录的 Codex CLI 指纹发生变化。",
        "runner_unavailable": "Codex Runner 当前不可用。",
        # Kept for historical failure-code compatibility. New r23 failures use
        # the two specific categories below.
        "invalid_output": (
            "模型未返回仅含 content、organization、language 三字段的有效 JSON 分数对象"
        ),
        "invalid_score_schema": (
            "模型未返回仅含 content、organization、language 三字段的有效 JSON 分数对象"
        ),
        "invalid_score_value": "模型返回的分数必须为 1–5，且间隔为 0.5。",
        "interrupted": "MCP 连接在调用期间中断。",
        "execution_error": "Codex 调用未成功完成。",
        "analysis_error": "统计报告生成失败。",
    }.get(code, "调用需要人工检查。")


class R23Service:
    def __init__(
        self,
        db: Session,
        *,
        runner_factory: Callable[[], CodexExecRunner] = CodexExecRunner,
        data_root: Path | None = None,
    ):
        self.db = db
        self.runner_factory = runner_factory
        self.data_root = data_root or Path(settings.R23_DRESS_ROOT)

    # ----- data gate -----
    def data_status(self) -> dict[str, Any]:
        try:
            return audit_data_root(self.data_root)
        except DataGateError as exc:
            return {
                "protocol_id": PROTOCOL_ID,
                "ready": False,
                "datasets": [],
                "operator_confirmation_required": True,
                "error": str(exc),
            }

    def _verified_data_status(self) -> dict[str, Any]:
        try:
            return audit_data_root(self.data_root)
        except DataGateError as exc:
            raise R23DomainError("data_drift", str(exc)) from exc

    # ----- runner configs -----
    def list_runner_configs(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(R23RunnerConfig).order_by(
                R23RunnerConfig.created_at, R23RunnerConfig.id
            )
        ).all()
        return [self.runner_out(row) for row in rows]

    def create_runner_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = RunnerConfigIn.model_validate(payload)
        config = value.model_dump(mode="json", exclude={"name"})
        row = R23RunnerConfig(
            id=str(uuid.uuid4()),
            name=value.name.strip(),
            model=value.model.strip(),
            reasoning_effort=value.reasoning_effort,
            speed_mode=value.speed_mode,
            timeout_seconds=value.timeout_seconds,
            config_sha256=canonical_sha256(config),
            status="ready",
        )
        self.db.add(row)
        self.db.commit()
        return self.runner_out(row)

    def delete_runner_config(self, config_id: str) -> None:
        row = self._runner(config_id)
        in_use = self.db.scalar(
            select(func.count())
            .select_from(R23ModelBinding)
            .where(R23ModelBinding.runner_config_id == config_id)
        )
        self._require(not in_use, "conflict", "runner is bound to a project")
        self.db.delete(row)
        self.db.commit()

    # ----- rubric versions -----
    def list_rubrics(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(R23RubricVersion).order_by(
                R23RubricVersion.created_at, R23RubricVersion.id
            )
        ).all()
        return [self.rubric_out(row) for row in rows]

    def create_rubric(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = RubricIn.model_validate(payload)
        text = value.rubric.replace("\r\n", "\n").replace("\r", "\n").strip()
        lowered = text.casefold()
        for required in ("content", "organization", "language"):
            self._require(
                required in lowered, "validation", f"rubric must define {required}"
            )
        row = R23RubricVersion(
            id=str(uuid.uuid4()),
            name=value.name.strip(),
            rubric_text=text,
            rubric_sha256=canonical_sha256(
                {"protocol_id": PROTOCOL_ID, "rubric": text}
            ),
            status="ready",
        )
        self.db.add(row)
        self.db.commit()
        return self.rubric_out(row)

    def delete_rubric(self, rubric_id: str) -> None:
        row = self._rubric(rubric_id)
        in_use = self.db.scalar(
            select(func.count())
            .select_from(R23Project)
            .where(R23Project.rubric_version_id == rubric_id)
        )
        self._require(not in_use, "conflict", "rubric is bound to a project")
        self.db.delete(row)
        self.db.commit()

    # ----- projects -----
    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(R23Project).order_by(R23Project.created_at.desc(), R23Project.id)
        ).all()
        return [self.project_out(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.project_out(self._project(project_id))

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        kind = str(payload.get("kind", ""))
        runner_id = str(payload.get("runner_config_id", ""))
        rubric_id = str(payload.get("rubric_id", ""))
        pilot_id = payload.get("pilot_project_id")
        confirmed = payload.get("data_processing_confirmed") is True
        self._require(0 < len(name) <= 120, "validation", "project name is required")
        self._require(
            kind in {item.value for item in ProjectKind},
            "validation",
            "invalid project kind",
        )
        self._require(bool(runner_id), "validation", "runner ID is required")
        self._require(
            confirmed,
            "validation",
            "restricted-data processing confirmation is required",
        )
        runner = self._runner(runner_id)
        rubric = self._rubric(rubric_id)
        data_status = self._verified_data_status()
        pilot: R23Project | None = None
        if kind == ProjectKind.FORMAL.value:
            if pilot_id:
                pilot = self._project(str(pilot_id))
                self._require(
                    pilot.kind == ProjectKind.PILOT.value
                    and pilot.status == "completed",
                    "validation",
                    "pilot must be a completed r23 technical pilot",
                )
        else:
            self._require(
                not pilot_id,
                "validation",
                "pilot projects cannot reference another pilot",
            )

        signature_value = {
            "kind": kind,
            "runner_sha256": runner.config_sha256,
            "rubric_sha256": rubric.rubric_sha256,
            "data_hashes": sorted(item["sha256"] for item in data_status["datasets"]),
            "protocol": canonical_protocol_manifest(),
        }
        formal_signature = (
            canonical_sha256(signature_value) if kind == "formal" else None
        )
        if pilot is not None:
            pilot_bindings = self._bindings(pilot.id)
            self._require(
                len(pilot_bindings) == 1
                and pilot_bindings[0].config_sha256 == runner.config_sha256,
                "validation",
                "formal runner must exactly match the completed pilot",
            )
            pilot_rubric = self._rubric(pilot.rubric_version_id)
            self._require(
                pilot_rubric.rubric_sha256 == rubric.rubric_sha256,
                "validation",
                "formal rubric must exactly match the completed pilot",
            )
            self._require(
                {item["sha256"] for item in pilot.data_status_json["datasets"]}
                == {item["sha256"] for item in data_status["datasets"]},
                "validation",
                "formal data hashes must exactly match the completed pilot",
            )

        row = R23Project(
            id=str(uuid.uuid4()),
            name=name,
            kind=kind,
            status="draft",
            rubric_version_id=rubric.id,
            pilot_project_id=pilot.id if pilot else None,
            formal_signature=formal_signature,
            data_processing_confirmed=True,
            data_processing_confirmed_at=utc_now_naive(),
            data_status_json=data_status,
            manifest_json={},
            results_embargoed=True,
        )
        self.db.add(row)
        self.db.flush()
        self.db.add(
            R23ModelBinding(
                id=str(uuid.uuid4()),
                project_id=row.id,
                runner_config_id=runner.id,
                position=1,
                model=runner.model,
                config_sha256=runner.config_sha256,
                frozen_runtime_json=runner.frozen_runtime_json or {},
            )
        )
        self._event(row.id, "project_created", {"kind": kind})
        self.db.commit()
        return self.project_out(row)

    def _prepare_run_snapshot(self, project: R23Project) -> R23Project:
        """Capture every executable input automatically when a draft starts."""

        self._require(
            project.status == "draft",
            "conflict",
            "only a draft project can create a run snapshot",
        )
        current_status = self._verified_data_status()
        self._require(
            {item["sha256"] for item in current_status["datasets"]}
            == {item["sha256"] for item in project.data_status_json["datasets"]},
            "data_drift",
            "DREsS_CASE files changed after project creation",
        )
        try:
            observations = materialize_sample(self.data_root, project.kind)
        except DataGateError as exc:
            raise R23DomainError("data_drift", str(exc)) from exc
        manifest = public_manifest(
            kind=project.kind, data_status=current_status, observations=observations
        )
        bindings = self._bindings(project.id)
        self._require(
            len(bindings) == 1,
            "conflict",
            "project must have exactly one model binding",
        )
        for binding in bindings:
            runner = self._runner(binding.runner_config_id)
            self._require(
                runner.config_sha256 == binding.config_sha256,
                "conflict",
                "runner configuration changed after project creation",
            )
            config = {
                "model": runner.model,
                "reasoning_effort": runner.reasoning_effort,
                "speed_mode": runner.speed_mode,
                "timeout_seconds": runner.timeout_seconds,
            }
            try:
                binding.frozen_runtime_json = self.runner_factory().frozen_runtime(
                    config
                )
            except RunnerUnavailableError as exc:
                raise R23DomainError("runner_unavailable", str(exc)) from exc

        binding = bindings[0]
        manifest["runner"] = {
            "model": binding.model,
            "reasoning_effort": binding.frozen_runtime_json["reasoning_effort"],
            "speed_mode": binding.frozen_runtime_json["speed_mode"],
            "service_tier": binding.frozen_runtime_json["service_tier"],
            "timeout_seconds": binding.frozen_runtime_json["timeout_seconds"],
            "config_sha256": binding.config_sha256,
            "cli_version": binding.frozen_runtime_json["cli_version"],
            "executable_sha256": binding.frozen_runtime_json["executable_sha256"],
        }
        manifest["rubric_sha256"] = self._rubric(
            project.rubric_version_id
        ).rubric_sha256

        observation_rows: dict[str, R23SampleObservation] = {}
        for item in observations:
            row = R23SampleObservation(
                project_id=project.id,
                observation_key=item.observation_key,
                dimension=item.dimension,
                source_id_hash=item.source_id_hash,
                label_x2=item.label_x2,
                prompt_sha256=item.prompt_sha256,
                input_sha256=item.input_sha256,
                word_count=item.word_count,
                derived_base_id=item.derived_base_id,
                corruption_repeat=item.corruption_repeat,
                collision=item.collision,
                rerun=item.rerun,
            )
            self.db.add(row)
            observation_rows[item.observation_key] = row
        self.db.flush()

        groups: dict[tuple[str, str, int], R23RunGroup] = {}
        order = 0
        for run_index in (0, 1):
            for dimension in ("content", "organization", "language"):
                for binding in bindings:
                    order += 1
                    group = R23RunGroup(
                        project_id=project.id,
                        model_binding_id=binding.id,
                        dimension=dimension,
                        run_index=run_index,
                        status="pending",
                        order_rank=order,
                        expected_calls=0,
                        completed_calls=0,
                    )
                    self.db.add(group)
                    groups[(binding.id, dimension, run_index)] = group
        self.db.flush()

        evaluations: dict[tuple[str, str, int], R23UniqueEvaluation] = {}
        evaluation_observations: list[
            tuple[R23UniqueEvaluation, R23SampleObservation]
        ] = []
        for binding in bindings:
            for item in observations:
                for run_index in (0, 1) if item.rerun else (0,):
                    key = (binding.id, item.input_sha256, run_index)
                    evaluation = evaluations.get(key)
                    if evaluation is None:
                        group = groups[(binding.id, item.dimension, run_index)]
                        evaluation = R23UniqueEvaluation(
                            project_id=project.id,
                            model_binding_id=binding.id,
                            run_group_id=group.id,
                            input_sha256=item.input_sha256,
                            run_index=run_index,
                            status="pending",
                            prompt_text=item.prompt,
                            essay_text=item.essay,
                        )
                        evaluations[key] = evaluation
                        group.expected_calls += 1
                    evaluation_observations.append(
                        (evaluation, observation_rows[item.observation_key])
                    )

        # A formal snapshot contains thousands of evaluations. Flush them as one
        # batch instead of making one database round-trip per evaluation.
        self.db.add_all(evaluations.values())
        self.db.flush()
        self.db.add_all(
            [
                R23EvaluationObservation(
                    evaluation_id=evaluation.id,
                    observation_id=observation.id,
                )
                for evaluation, observation in evaluation_observations
            ]
        )

        manifest["actual_unique_logical_calls"] = len(evaluations)
        manifest["actual_unique_primary_calls"] = sum(
            evaluation.run_index == 0 for evaluation in evaluations.values()
        )
        manifest["actual_unique_rerun_calls"] = sum(
            evaluation.run_index == 1 for evaluation in evaluations.values()
        )
        project.manifest_json = manifest
        project.manifest_sha256 = canonical_sha256(manifest)
        project.status = "ready"
        project.frozen_at = utc_now_naive()
        self._event(
            project.id,
            "run_snapshot_created",
            {
                "manifest_sha256": project.manifest_sha256,
                "logical_calls": len(evaluations),
            },
        )
        self.db.commit()
        return project

    def start_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status in {"draft", "ready", "frozen"},
            "conflict",
            "only a project that has not started can start",
        )
        self._require(
            project.data_processing_confirmed,
            "validation",
            "data processing confirmation is missing",
        )
        if project.kind == "formal" and project.pilot_project_id:
            pilot = self._project(project.pilot_project_id)
            self._require(
                pilot.status == "completed",
                "conflict",
                "matching pilot is not complete",
            )
        if project.status == "draft":
            project = self._prepare_run_snapshot(project)
        project.status = "running"
        project.started_at = utc_now_naive()
        groups = self.db.scalars(
            select(R23RunGroup).where(R23RunGroup.project_id == project.id)
        ).all()
        for group in groups:
            group.status = "queued" if group.expected_calls else "completed"
        self._event(project.id, "project_started", {})
        self.db.commit()
        return self.project_out(project)

    def repeat_project(self, project_id: str) -> dict[str, Any]:
        """Create an independent draft run with the same auditable inputs."""

        source = self._project(project_id)
        self._require(
            source.status == "completed",
            "conflict",
            "only a completed project can be run again",
        )
        bindings = self._bindings(source.id)
        self._require(
            len(bindings) == 1,
            "conflict",
            "repeat requires exactly one model binding",
        )
        suffix = 2
        while True:
            ending = f" · repeat {suffix}"
            name = f"{source.name[: 120 - len(ending)]}{ending}"
            exists = self.db.scalar(
                select(func.count())
                .select_from(R23Project)
                .where(R23Project.name == name)
            )
            if not exists:
                break
            suffix += 1
        repeated = self.create_project(
            {
                "name": name,
                "kind": source.kind,
                "runner_config_id": bindings[0].runner_config_id,
                "rubric_id": source.rubric_version_id,
                "pilot_project_id": source.pilot_project_id,
                "data_processing_confirmed": True,
            }
        )
        self._event(
            repeated["id"],
            "project_repeated_from",
            {"source_project_id": source.id},
        )
        self.db.commit()
        return self.get_project(repeated["id"])

    def pause_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status == "running", "conflict", "only a running project can pause"
        )
        project.status = "paused"
        self._event(project.id, "project_paused", {})
        self.db.commit()
        return self.project_out(project)

    def resume_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status in {"paused", "attention_required"},
            "conflict",
            "only a paused or resolved project can resume",
        )
        failures = self.db.scalar(
            select(func.count())
            .select_from(R23UniqueEvaluation)
            .where(
                R23UniqueEvaluation.project_id == project.id,
                R23UniqueEvaluation.status == "attention_required",
            )
        )
        self._require(
            not failures, "conflict", "retry or terminate every failed call first"
        )
        project.status = "running"
        self._event(project.id, "project_resumed", {})
        self.db.commit()
        return self.project_out(project)

    def terminate_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(
            project.status not in {"completed", "terminated"},
            "conflict",
            "project is already terminal",
        )
        project.status = "terminated"
        project.terminated_at = utc_now_naive()
        self.db.execute(
            R23UniqueEvaluation.__table__.update()
            .where(
                R23UniqueEvaluation.project_id == project.id,
                R23UniqueEvaluation.status.in_(("pending", "leased")),
            )
            .values(status="terminated", worker_id=None, lease_until=None)
        )
        self._event(project.id, "project_terminated", {})
        self.db.commit()
        return self.project_out(project)

    def delete_project(self, project_id: str) -> None:
        project = self._project(project_id)
        self._require(
            project.status in {"draft", "terminated"},
            "forbidden",
            "started or completed projects are immutable",
        )
        self.db.delete(project)
        self.db.commit()

    # ----- monitoring / retry -----
    def list_groups(self, project_id: str) -> list[dict[str, Any]]:
        self._project(project_id)
        rows = self.db.scalars(
            select(R23RunGroup)
            .where(R23RunGroup.project_id == project_id)
            .order_by(R23RunGroup.order_rank)
        ).all()
        return [self.group_out(row) for row in rows]

    def list_calls(self, project_id: str, *, limit: int = 500) -> dict[str, Any]:
        project = self._project(project_id)
        rows = self.db.scalars(
            select(R23UniqueEvaluation)
            .where(R23UniqueEvaluation.project_id == project_id)
            .order_by(
                case(
                    (R23UniqueEvaluation.status == "attention_required", 0),
                    else_=1,
                ),
                R23UniqueEvaluation.id.desc(),
            )
            .limit(min(max(limit, 1), 2_000))
        ).all()
        return {
            "results_embargoed": project.results_embargoed,
            "items": [
                self.call_out(row, embargoed=project.results_embargoed) for row in rows
            ],
        }

    def retry_call(self, project_id: str, call_id: int) -> dict[str, Any]:
        project = self._project(project_id)
        row = self.db.get(R23UniqueEvaluation, call_id)
        self._require(
            bool(row and row.project_id == project.id), "not_found", "call not found"
        )
        assert row is not None
        self._require(
            row.status == "attention_required",
            "conflict",
            "only a failed call can be retried",
        )
        group = self.db.get(R23RunGroup, row.run_group_id)
        self._require(bool(group), "not_found", "run group not found")
        assert group is not None
        row.status = "pending"
        row.failure_code = None
        row.failure_summary = None
        row.worker_id = None
        row.lease_until = None
        group.status = "running"
        project.status = "running"
        self._event(
            project.id,
            "call_retried",
            {"call_id": row.id, "input_sha256": row.input_sha256},
        )
        self.db.commit()
        return self.call_out(row, embargoed=True)

    # ----- report / exports -----
    def get_analysis(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        if project.results_embargoed:
            return {
                "results_embargoed": True,
                "status": project.status,
                "message": "正式报告锁定前不返回分数、趋势或中间效应。",
            }
        report = self.db.get(R23LockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        return {"results_embargoed": False, "report": report.report_json}

    def get_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        report = self.db.get(R23LockedReport, project.id)
        if report is None:
            return {
                "results_embargoed": True,
                "status": project.status,
                "report": None,
            }
        if project.kind == "pilot_run":
            return {
                "results_embargoed": True,
                "status": project.status,
                "report": report.report_json,
                "report_sha256": report.report_sha256,
            }
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        return {
            "results_embargoed": False,
            "status": project.status,
            "report": report.report_json,
            "report_sha256": report.report_sha256,
            "figure_sha256": report.figure_sha256,
        }

    def export_manifest(self, project_id: str) -> tuple[str, str]:
        project = self._project(project_id)
        self._require(
            bool(project.manifest_sha256),
            "conflict",
            "the project has not created a run snapshot yet",
        )
        payload = json.dumps(
            project.manifest_json, ensure_ascii=False, sort_keys=True, indent=2
        )
        return payload, project.manifest_sha256 or canonical_sha256(
            project.manifest_json
        )

    def export_results_csv(self, project_id: str) -> tuple[str, str]:
        project = self._project(project_id)
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "protocol_id",
                "model_binding_id",
                "observation_key",
                "dimension",
                "case_label",
                "content",
                "organization",
                "language",
                "run_index",
                "input_sha256",
                "collision",
            ]
        )
        query = (
            select(R23UniqueEvaluation, R23SampleObservation)
            .join(
                R23EvaluationObservation,
                R23EvaluationObservation.evaluation_id == R23UniqueEvaluation.id,
            )
            .join(
                R23SampleObservation,
                R23SampleObservation.id == R23EvaluationObservation.observation_id,
            )
            .where(
                R23UniqueEvaluation.project_id == project.id,
                R23UniqueEvaluation.status == "succeeded",
            )
            .order_by(R23UniqueEvaluation.id, R23SampleObservation.id)
        )
        for evaluation, observation in self.db.execute(query).all():
            writer.writerow(
                [
                    PROTOCOL_ID,
                    evaluation.model_binding_id,
                    observation.observation_key,
                    observation.dimension,
                    observation.label_x2 / 2,
                    (evaluation.content_score_x2 or 0) / 2,
                    (evaluation.organization_score_x2 or 0) / 2,
                    (evaluation.language_score_x2 or 0) / 2,
                    evaluation.run_index,
                    evaluation.input_sha256,
                    observation.collision,
                ]
            )
        payload = output.getvalue()
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def export_report_json(self, project_id: str) -> tuple[str, str]:
        project = self._project(project_id)
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        report = self.db.get(R23LockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        payload = json.dumps(
            report.report_json, ensure_ascii=False, sort_keys=True, indent=2
        )
        return payload, report.report_sha256

    def export_figure_svg(self, project_id: str) -> tuple[str, str]:
        project = self._project(project_id)
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        report = self.db.get(R23LockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        return report.figure_svg, report.figure_sha256

    # ----- finalization called by executor -----
    def finalize_if_ready(self, project_id: str) -> bool:
        project = self._project(project_id)
        remaining = int(
            self.db.scalar(
                select(func.count())
                .select_from(R23UniqueEvaluation)
                .where(
                    R23UniqueEvaluation.project_id == project.id,
                    R23UniqueEvaluation.status != "succeeded",
                )
            )
            or 0
        )
        if remaining:
            return False
        groups = self.db.scalars(
            select(R23RunGroup).where(R23RunGroup.project_id == project.id)
        ).all()
        now = utc_now_naive()
        for group in groups:
            group.status = "completed"
            group.completed_calls = group.expected_calls
            group.completed_at = group.completed_at or now
        if project.kind == "pilot_run":
            report_json = self._pilot_report(project)
            svg = self._sealed_svg(
                "技术试点完成", "仅报告格式、失败率与延迟；不显示任何评分结果。"
            )
            self._lock_report(project, report_json, svg, release=False)
        else:
            project.status = "analyzing"
            self.db.commit()
            try:
                report_json = self._formal_report(project)
                svg = self._report_svg(report_json)
            except Exception as exc:
                project.status = "attention_required"
                self._event(
                    project.id, "analysis_failed", {"error_code": "analysis_error"}
                )
                self.db.commit()
                raise R23DomainError(
                    "analysis_error", _safe_error("analysis_error")
                ) from exc
            self._lock_report(project, report_json, svg, release=True)
        project.status = "completed"
        project.completed_at = now
        self._event(
            project.id, "report_locked", {"report_sha256": project.report_sha256}
        )
        self.db.commit()
        return True

    def _pilot_report(self, project: R23Project) -> dict[str, Any]:
        attempts = self.db.scalars(
            select(R23CallAttempt).where(R23CallAttempt.project_id == project.id)
        ).all()
        latencies = sorted(
            item.latency_ms for item in attempts if item.status == "succeeded"
        )
        counts = Counter(item.status for item in attempts)
        return {
            "protocol_id": PROTOCOL_ID,
            "report_type": "technical_pilot",
            "results_embargoed": True,
            "logical_calls": len(attempts),
            "status_counts": dict(sorted(counts.items())),
            "failure_rate": (
                (counts.get("failed", 0) / len(attempts)) if attempts else 0.0
            ),
            "latency_ms": {
                "p50": statistics.median(latencies) if latencies else None,
                "p95": _percentile(latencies, 0.95),
            },
            "disclosure": "技术试点不显示分数、趋势或效应，也不用于确认性推断。",
        }

    def _formal_report(self, project: R23Project) -> dict[str, Any]:
        from app.experiment.r23.analysis import AnalysisRow, build_report

        query = (
            select(R23UniqueEvaluation, R23SampleObservation)
            .join(
                R23EvaluationObservation,
                R23EvaluationObservation.evaluation_id == R23UniqueEvaluation.id,
            )
            .join(
                R23SampleObservation,
                R23SampleObservation.id == R23EvaluationObservation.observation_id,
            )
            .where(
                R23UniqueEvaluation.project_id == project.id,
                R23UniqueEvaluation.run_index == 0,
                R23UniqueEvaluation.status == "succeeded",
            )
        )
        rows = [
            AnalysisRow(
                model_binding_id=evaluation.model_binding_id,
                dimension=observation.dimension,
                cluster_id=observation.derived_base_id or observation.prompt_sha256,
                prompt_sha256=observation.prompt_sha256,
                input_sha256=observation.input_sha256,
                label_x2=observation.label_x2,
                content_x2=int(evaluation.content_score_x2),
                organization_x2=int(evaluation.organization_score_x2),
                language_x2=int(evaluation.language_score_x2),
                word_count=observation.word_count,
            )
            for evaluation, observation in self.db.execute(query).all()
        ]
        report = build_report(rows)
        report["repeatability"] = self._repeatability(project.id)
        report["manifest_sha256"] = project.manifest_sha256
        report["model_bindings"] = [
            {
                "id": binding.id,
                "position": binding.position,
                "model": binding.model,
                "reasoning_effort": binding.frozen_runtime_json["reasoning_effort"],
                "speed_mode": binding.frozen_runtime_json["speed_mode"],
                "service_tier": binding.frozen_runtime_json["service_tier"],
                "config_sha256": binding.config_sha256,
            }
            for binding in self._bindings(project.id)
        ]
        return report

    def _repeatability(self, project_id: str) -> dict[str, Any]:
        evaluations = self.db.scalars(
            select(R23UniqueEvaluation).where(
                R23UniqueEvaluation.project_id == project_id,
                R23UniqueEvaluation.status == "succeeded",
            )
        ).all()
        paired: dict[tuple[str, str], dict[int, R23UniqueEvaluation]] = defaultdict(
            dict
        )
        for row in evaluations:
            paired[(row.model_binding_id, row.input_sha256)][row.run_index] = row
        by_model: dict[str, list[float]] = defaultdict(list)
        exact: Counter[str] = Counter()
        totals: Counter[str] = Counter()
        for (model_id, _), values in paired.items():
            if 0 not in values or 1 not in values:
                continue
            first, second = values[0], values[1]
            left = [
                first.content_score_x2,
                first.organization_score_x2,
                first.language_score_x2,
            ]
            right = [
                second.content_score_x2,
                second.organization_score_x2,
                second.language_score_x2,
            ]
            diffs = [abs(int(a) - int(b)) / 2 for a, b in zip(left, right, strict=True)]
            by_model[model_id].extend(diffs)
            exact[model_id] += int(left == right)
            totals[model_id] += 1
        return {
            model_id: {
                "paired_inputs": totals[model_id],
                "three_channel_exact_rate": (
                    exact[model_id] / totals[model_id] if totals[model_id] else None
                ),
                "mean_absolute_channel_difference": (
                    float(np_mean(values)) if values else None
                ),
            }
            for model_id, values in by_model.items()
        }

    def _lock_report(
        self, project: R23Project, report_json: dict, svg: str, *, release: bool
    ) -> None:
        report_hash = canonical_sha256(report_json)
        figure_hash = hashlib.sha256(svg.encode("utf-8")).hexdigest()
        self.db.add(
            R23LockedReport(
                project_id=project.id,
                report_json=report_json,
                report_sha256=report_hash,
                figure_svg=svg,
                figure_sha256=figure_hash,
            )
        )
        project.report_sha256 = report_hash
        project.results_embargoed = not release

    @staticmethod
    def _sealed_svg(title: str, message: str) -> str:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="360" viewBox="0 0 960 360" role="img" '
            f'aria-label="{title}"><rect width="960" height="360" fill="#f8fafc"/>'
            f'<text x="480" y="170" text-anchor="middle" font-size="28" fill="#1e293b">{title}</text>'
            f'<text x="480" y="215" text-anchor="middle" font-size="16" fill="#64748b">{message}</text></svg>'
        )

    @staticmethod
    def _report_svg(report: dict[str, Any]) -> str:
        cells = report.get("cells", [])
        bars = []
        for index, cell in enumerate(cells):
            value = max(0.0, min(1.0, float(cell.get("mpa", 0))))
            x = 90 + index * 130
            height = value * 220
            bars.append(
                f'<rect x="{x}" y="{290 - height:.1f}" width="72" height="{height:.1f}" rx="7" fill="#6366f1"/>'
                f'<text x="{x + 36}" y="318" text-anchor="middle" font-size="12" fill="#475569">{cell.get("dimension", "")[:4]}</text>'
            )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="360" viewBox="0 0 960 360" role="img" '
            'aria-label="模型各维度 MPA"><rect width="960" height="360" fill="#ffffff"/>'
            '<text x="48" y="38" font-size="20" font-weight="600" fill="#0f172a">MPA（95% CI 见统计报告）</text>'
            '<line x1="60" y1="290" x2="900" y2="290" stroke="#cbd5e1"/>'
            + "".join(bars)
            + "</svg>"
        )

    # ----- serializers and lookup -----
    @staticmethod
    def runner_out(row: R23RunnerConfig) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "model": row.model,
            "reasoning_effort": row.reasoning_effort,
            "speed_mode": row.speed_mode,
            "timeout_seconds": row.timeout_seconds,
            "config_sha256": row.config_sha256,
            "status": row.status,
            "created_at": row.created_at,
        }

    @staticmethod
    def rubric_out(row: R23RubricVersion) -> dict[str, Any]:
        return {
            "id": row.id,
            "protocol_id": row.protocol_id,
            "name": row.name,
            "rubric": row.rubric_text,
            "rubric_sha256": row.rubric_sha256,
            "status": row.status,
            "created_at": row.created_at,
        }

    def project_out(self, row: R23Project) -> dict[str, Any]:
        counts = dict(
            self.db.execute(
                select(R23UniqueEvaluation.status, func.count())
                .where(R23UniqueEvaluation.project_id == row.id)
                .group_by(R23UniqueEvaluation.status)
            ).all()
        )
        bindings = self._bindings(row.id)
        total = sum(counts.values())
        latencies = self.db.scalars(
            select(R23UniqueEvaluation.latency_ms).where(
                R23UniqueEvaluation.project_id == row.id,
                R23UniqueEvaluation.latency_ms.is_not(None),
            )
        ).all()
        return {
            "id": row.id,
            "protocol_id": row.protocol_id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "rubric_id": row.rubric_version_id,
            "pilot_project_id": row.pilot_project_id,
            "runner_bindings": [
                {
                    "id": binding.id,
                    "runner_config_id": binding.runner_config_id,
                    "position": binding.position,
                    "model": binding.model,
                    "reasoning_effort": (
                        binding.frozen_runtime_json.get("reasoning_effort")
                        or self._runner(binding.runner_config_id).reasoning_effort
                    ),
                    "speed_mode": (
                        binding.frozen_runtime_json.get("speed_mode")
                        or self._runner(binding.runner_config_id).speed_mode
                    ),
                    "timeout_seconds": (
                        binding.frozen_runtime_json.get("timeout_seconds")
                        or self._runner(binding.runner_config_id).timeout_seconds
                    ),
                    "config_sha256": binding.config_sha256,
                }
                for binding in bindings
            ],
            "data_processing_confirmed": row.data_processing_confirmed,
            "data_status": row.data_status_json,
            "manifest_sha256": row.manifest_sha256,
            "manifest_summary": {
                key: value
                for key, value in row.manifest_json.items()
                if key not in {"observations"}
            },
            "results_embargoed": row.results_embargoed,
            "report_sha256": row.report_sha256,
            "progress": {"total": total, **counts},
            "performance": {
                "p50_latency_ms": statistics.median(latencies) if latencies else None,
                "p95_latency_ms": _percentile(latencies, 0.95),
            },
            "created_at": row.created_at,
            "snapshot_at": row.frozen_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    @staticmethod
    def group_out(row: R23RunGroup) -> dict[str, Any]:
        return {
            "id": row.id,
            "model_binding_id": row.model_binding_id,
            "dimension": row.dimension,
            "run_index": row.run_index,
            "status": row.status,
            "order_rank": row.order_rank,
            "expected_calls": row.expected_calls,
            "completed_calls": row.completed_calls,
        }

    @staticmethod
    def call_out(row: R23UniqueEvaluation, *, embargoed: bool) -> dict[str, Any]:
        value = {
            "id": row.id,
            "model_binding_id": row.model_binding_id,
            "run_group_id": row.run_group_id,
            "run_index": row.run_index,
            "status": row.status,
            "input_sha256": row.input_sha256,
            "attempt_count": row.attempt_count,
            "latency_ms": row.latency_ms,
            "failure_code": row.failure_code,
            # Stored summaries are immutable audit data and may predate the
            # current safe wording. Always render the canonical safe summary.
            "failure_summary": (
                _safe_error(row.failure_code)
                if row.failure_code is not None
                else row.failure_summary
            ),
        }
        if not embargoed:
            value["score_x2"] = {
                "content": row.content_score_x2,
                "organization": row.organization_score_x2,
                "language": row.language_score_x2,
            }
        return value

    def _runner(self, config_id: str) -> R23RunnerConfig:
        row = self.db.get(R23RunnerConfig, config_id)
        self._require(bool(row), "not_found", "runner not found")
        assert row is not None
        return row

    def _rubric(self, rubric_id: str) -> R23RubricVersion:
        row = self.db.get(R23RubricVersion, rubric_id)
        self._require(bool(row), "not_found", "rubric not found")
        assert row is not None
        return row

    def _project(self, project_id: str) -> R23Project:
        row = self.db.get(R23Project, project_id)
        self._require(bool(row), "not_found", "project not found")
        assert row is not None
        return row

    def _bindings(self, project_id: str) -> list[R23ModelBinding]:
        return list(
            self.db.scalars(
                select(R23ModelBinding)
                .where(R23ModelBinding.project_id == project_id)
                .order_by(R23ModelBinding.position)
            ).all()
        )

    def _event(self, project_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self.db.add(
            R23Event(project_id=project_id, event_type=event_type, detail_json=detail)
        )

    @staticmethod
    def _require(condition: bool, code: str, message: str) -> None:
        if not condition:
            raise R23DomainError(code, message)


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def np_mean(values: list[float]) -> float:
    return sum(values) / len(values)


__all__ = ["R23DomainError", "R23Service", "canonical_sha256"]
