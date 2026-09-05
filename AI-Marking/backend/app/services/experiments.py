"""Framework-neutral unified experiment lifecycle, audit, export, and report service.

Mirrors the frozen r23 service in shape (data gate, runner/rubric/project CRUD,
immutable run snapshots, manual retry, embargoed report locking, hashed
exports) but every rule that varies by study is delegated to the registered
dataset adapter and research template.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.core.contracts import (
    DEFAULT_SPEED_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    REASONING_EFFORT,
    ScoringContract,
    contract_from_payload,
)
from app.experiment.core.registry import (
    get_dataset,
    get_template,
    list_templates,
)
from app.experiment.r21.codex_runner import CodexExecRunner, RunnerUnavailableError
from app.models.experiments import (
    ExpCallAttempt,
    ExpCallScore,
    ExpDataset,
    ExpDatasetRevision,
    ExpEvent,
    ExpInput,
    ExpInputLabel,
    ExpLockedReport,
    ExpProject,
    ExpProjectRunner,
    ExpRubricVersion,
    ExpRunnerConfig,
    ExpRunGroup,
    ExpScoringContract,
    ExpUniqueEvaluation,
)


class ExperimentDomainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def safe_error(code: str) -> str:
    return {
        "timeout": "Codex 调用超时；未自动重试。",
        "runner_drift": "开始实验时记录的 Codex CLI 指纹发生变化。",
        "runner_unavailable": "Codex Runner 当前不可用。",
        "invalid_score_schema": (
            "模型未返回有效的 JSON 分数对象（缺失、多余或类型非法的字段）。"
        ),
        "invalid_score_value": "模型返回的分数不在评分契约的合法网格上。",
        "interrupted": "MCP 连接在调用期间中断。",
        "execution_error": "Codex 调用未成功完成。",
        "analysis_error": "统计报告生成失败。",
    }.get(code, "调用需要人工检查。")


class RunnerConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: Literal["low", "medium", "high"] = REASONING_EFFORT
    speed_mode: Literal["standard", "fast"] = DEFAULT_SPEED_MODE
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=MIN_TIMEOUT_SECONDS, le=MAX_TIMEOUT_SECONDS
    )


class RubricIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=96)
    name: str = Field(min_length=1, max_length=120)
    rubric: str = Field(min_length=80, max_length=24_000)


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["pilot_run", "formal"]
    template_id: str = Field(min_length=1, max_length=96)
    dataset_revision_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    runner_config_ids: list[str] = Field(min_length=1, max_length=4)
    pilot_project_id: str | None = None
    data_processing_confirmed: bool = False


class ExperimentService:
    def __init__(
        self,
        db: Session,
        *,
        runner_factory: Callable[[], CodexExecRunner] = CodexExecRunner,
        datasets_root: Path | None = None,
    ):
        self.db = db
        self.runner_factory = runner_factory
        self.datasets_root = datasets_root or Path(settings.EXP_DATASETS_ROOT)

    # ----- dataset gate -----
    def _dataset_root(self, dataset_key: str) -> Path:
        return self.datasets_root

    def data_status(self, dataset_key: str) -> dict[str, Any]:
        """Audit one registered dataset; never raise for gate failures."""
        try:
            adapter = get_dataset(dataset_key)
        except KeyError as exc:
            raise ExperimentDomainError("not_found", str(exc)) from exc
        try:
            audit = adapter.audit(self._dataset_root(dataset_key))
        except ValueError as exc:
            return {
                "dataset_key": dataset_key,
                "ready": False,
                "operator_confirmation_required": True,
                "error": str(exc),
            }
        return {
            "dataset_key": dataset_key,
            "name": adapter.name,
            "access_level": adapter.access_level,
            "license_note": adapter.license_note,
            "ready": True,
            "report": audit.report,
            "contract": adapter.contract().manifest(),
        }

    def ensure_dataset_revision(
        self, dataset_key: str
    ) -> tuple[ExpDataset, ExpDatasetRevision, ExpScoringContract]:
        """Idempotently pin the current dataset state as a revision row."""
        adapter = get_dataset(dataset_key)
        audit = adapter.audit(self._dataset_root(dataset_key))
        dataset_row = self.db.scalar(
            select(ExpDataset).where(ExpDataset.key == dataset_key)
        )
        if dataset_row is None:
            dataset_row = ExpDataset(
                id=str(uuid.uuid4()),
                key=dataset_key,
                name=adapter.name,
                access_level=adapter.access_level,
                license_note=adapter.license_note,
            )
            self.db.add(dataset_row)
            self.db.flush()

        label = f"{dataset_key}-{canonical_sha256(audit.report['datasets'])[:8]}"
        revision_row = self.db.scalar(
            select(ExpDatasetRevision).where(
                ExpDatasetRevision.dataset_id == dataset_row.id,
                ExpDatasetRevision.revision_label == label,
            )
        )
        if revision_row is None:
            revision_row = ExpDatasetRevision(
                id=str(uuid.uuid4()),
                dataset_id=dataset_row.id,
                revision_label=label,
                file_specs_json=audit.report["datasets"],
                audit_json=audit.report,
                status="verified",
            )
            self.db.add(revision_row)
            self.db.flush()
        elif revision_row.audit_json != audit.report:
            raise ExperimentDomainError(
                "data_drift", f"{dataset_key} dataset changed after revision pinning"
            )

        contract = adapter.contract()
        contract_row = self.db.scalar(
            select(ExpScoringContract).where(
                ExpScoringContract.dataset_revision_id == revision_row.id
            )
        )
        if contract_row is None:
            contract_row = ExpScoringContract(
                id=str(uuid.uuid4()),
                dataset_revision_id=revision_row.id,
                channels_json=contract.manifest()["channels"],
                grid_min_x2=contract.grid_min_x2,
                grid_max_x2=contract.grid_max_x2,
                schema_sha256=contract.schema_sha256(),
            )
            self.db.add(contract_row)
            self.db.flush()
        elif contract_row.schema_sha256 != contract.schema_sha256():
            raise ExperimentDomainError(
                "data_drift", "scoring contract changed after revision pinning"
            )
        self.db.commit()
        return dataset_row, revision_row, contract_row

    def revision_contract(self, revision_id: str) -> ScoringContract:
        row = self.db.get(ExpScoringContract, revision_id)
        if row is not None:
            return contract_from_payload(
                {
                    "channels": row.channels_json,
                    "grid_min_x2": row.grid_min_x2,
                    "grid_max_x2": row.grid_max_x2,
                }
            )
        row = self.db.scalar(
            select(ExpScoringContract).where(
                ExpScoringContract.dataset_revision_id == revision_id
            )
        )
        self._require(bool(row), "not_found", "scoring contract not found")
        assert row is not None
        return contract_from_payload(
            {
                "channels": row.channels_json,
                "grid_min_x2": row.grid_min_x2,
                "grid_max_x2": row.grid_max_x2,
            }
        )

    # ----- templates -----
    def list_templates(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for template in list_templates():
            adapter = get_dataset(template.dataset_key)
            result.append(
                {
                    "template_id": template.template_id,
                    "name": template.name,
                    "dataset_key": template.dataset_key,
                    "runner_count": template.runner_count,
                    "require_runner_alignment": template.require_runner_alignment,
                    "sampling_seed": template.seed,
                    "contract": adapter.contract().manifest(),
                }
            )
        return result

    # ----- runner configs -----
    def list_runner_configs(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(ExpRunnerConfig).order_by(ExpRunnerConfig.created_at, ExpRunnerConfig.id)
        ).all()
        return [self.runner_out(row) for row in rows]

    def create_runner_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = RunnerConfigIn.model_validate(payload)
        config = value.model_dump(mode="json")
        row = ExpRunnerConfig(
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
            .select_from(ExpProjectRunner)
            .where(ExpProjectRunner.runner_config_id == config_id)
        )
        self._require(not in_use, "conflict", "runner is bound to a project")
        self.db.delete(row)
        self.db.commit()

    # ----- rubric versions -----
    def list_rubrics(self, template_id: str | None = None) -> list[dict[str, Any]]:
        query = select(ExpRubricVersion).order_by(
            ExpRubricVersion.created_at, ExpRubricVersion.id
        )
        if template_id:
            query = query.where(ExpRubricVersion.template_id == template_id)
        rows = self.db.scalars(query).all()
        return [self.rubric_out(row) for row in rows]

    def create_rubric(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = RubricIn.model_validate(payload)
        try:
            template = get_template(value.template_id)
        except KeyError as exc:
            raise ExperimentDomainError("not_found", str(exc)) from exc
        text = value.rubric.replace("\r\n", "\n").replace("\r", "\n").strip()
        lowered = text.casefold()
        contract = get_dataset(template.dataset_key).contract()
        for channel in contract.channels:
            self._require(
                channel.key in lowered,
                "validation",
                f"rubric must define {channel.key}",
            )
        row = ExpRubricVersion(
            id=str(uuid.uuid4()),
            template_id=template.template_id,
            name=value.name.strip(),
            rubric_text=text,
            rubric_sha256=canonical_sha256(
                {"template_id": template.template_id, "rubric": text}
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
            .select_from(ExpProject)
            .where(ExpProject.rubric_version_id == rubric_id)
        )
        self._require(not in_use, "conflict", "rubric is bound to a project")
        self.db.delete(row)
        self.db.commit()

    # ----- projects -----
    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(ExpProject).order_by(ExpProject.created_at.desc(), ExpProject.id)
        ).all()
        return [self.project_out(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.project_out(self._project(project_id))

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = ProjectIn.model_validate(payload)
        try:
            template = get_template(value.template_id)
        except KeyError as exc:
            raise ExperimentDomainError("not_found", str(exc)) from exc
        self._require(
            value.data_processing_confirmed,
            "validation",
            "restricted-data processing confirmation is required",
        )
        revision_row = self.db.get(ExpDatasetRevision, value.dataset_revision_id)
        self._require(bool(revision_row), "not_found", "dataset revision not found")
        assert revision_row is not None
        dataset_row = self.db.get(ExpDataset, revision_row.dataset_id)
        self._require(bool(dataset_row), "not_found", "dataset not found")
        assert dataset_row is not None
        self._require(
            dataset_row.key == template.dataset_key,
            "validation",
            "dataset revision does not belong to the template's dataset",
        )
        rubric = self._rubric(value.rubric_id)
        self._require(
            rubric.template_id == template.template_id,
            "validation",
            "rubric does not belong to the template",
        )
        self._require(
            len(set(value.runner_config_ids)) == len(value.runner_config_ids),
            "validation",
            "runner configs must be distinct",
        )
        self._require(
            len(value.runner_config_ids) == template.runner_count,
            "validation",
            f"template {template.template_id} requires exactly "
            f"{template.runner_count} runner configs",
        )
        runners = [self._runner(runner_id) for runner_id in value.runner_config_ids]
        if template.require_runner_alignment:
            aligned_attributes = {
                (
                    runner.reasoning_effort,
                    runner.speed_mode,
                    runner.timeout_seconds,
                )
                for runner in runners
            }
            self._require(
                len(aligned_attributes) == 1,
                "validation",
                "comparison templates require identical reasoning effort, speed, "
                "and timeout across runners",
            )
            self._require(
                len({runner.model for runner in runners}) == len(runners),
                "validation",
                "comparison runners must differ only by model identity",
            )
        pilot: ExpProject | None = None
        if value.kind == "formal" and value.pilot_project_id:
            pilot = self._project(value.pilot_project_id)
            self._require(
                pilot.kind == "pilot_run"
                and pilot.status == "completed"
                and pilot.template_id == template.template_id,
                "validation",
                "pilot must be a completed technical pilot of the same template",
            )
        else:
            self._require(
                not value.pilot_project_id,
                "validation",
                "pilot projects cannot reference another pilot",
            )
        if pilot is not None:
            self._require(
                pilot.dataset_revision_id == revision_row.id,
                "validation",
                "formal dataset revision must exactly match the completed pilot",
            )
            pilot_rubric = self._rubric(pilot.rubric_version_id) if pilot.rubric_version_id else None
            self._require(
                bool(pilot_rubric and pilot_rubric.rubric_sha256 == rubric.rubric_sha256),
                "validation",
                "formal rubric must exactly match the completed pilot",
            )
            pilot_runner_shas = sorted(
                binding.config_sha256 for binding in self._bindings(pilot.id)
            )
            self._require(
                pilot_runner_shas == sorted(runner.config_sha256 for runner in runners),
                "validation",
                "formal runner configs must exactly match the completed pilot",
            )

        contract = adapter_contract_for(self.db, revision_row)
        formal_signature = None
        if value.kind == "formal":
            formal_signature = canonical_sha256(
                {
                    "kind": "formal",
                    "template_id": template.template_id,
                    "runner_config_sha256": sorted(
                        runner.config_sha256 for runner in runners
                    ),
                    "rubric_sha256": rubric.rubric_sha256,
                    "dataset_file_sha256": sorted(
                        str(spec["sha256"])
                        for spec in revision_row.file_specs_json
                    ),
                    "contract": contract.payload(),
                    "sampling_seed": template.seed,
                }
            )
        row = ExpProject(
            id=str(uuid.uuid4()),
            template_id=template.template_id,
            name=value.name.strip(),
            kind=value.kind,
            status="draft",
            dataset_revision_id=revision_row.id,
            rubric_version_id=rubric.id,
            pilot_project_id=pilot.id if pilot else None,
            formal_signature=formal_signature,
            data_processing_confirmed=True,
            data_processing_confirmed_at=utc_now_naive(),
            data_status_json=revision_row.audit_json,
            manifest_json={},
            results_embargoed=True,
        )
        self.db.add(row)
        self.db.flush()
        for position, runner in enumerate(runners, start=1):
            self.db.add(
                ExpProjectRunner(
                    id=str(uuid.uuid4()),
                    project_id=row.id,
                    runner_config_id=runner.id,
                    position=position,
                    model=runner.model,
                    config_sha256=runner.config_sha256,
                    frozen_runtime_json=runner.frozen_runtime_json or {},
                )
            )
        self._event(row.id, "project_created", {"kind": value.kind})
        self.db.commit()
        return self.project_out(row)

    def _prepare_run_snapshot(self, project: ExpProject) -> ExpProject:
        self._require(
            project.status == "draft",
            "conflict",
            "only a draft project can create a run snapshot",
        )
        template = get_template(project.template_id)
        adapter = get_dataset(template.dataset_key)
        revision_row = self.db.get(ExpDatasetRevision, project.dataset_revision_id)
        self._require(bool(revision_row), "not_found", "dataset revision is missing")
        assert revision_row is not None
        try:
            audit = adapter.audit(self._dataset_root(template.dataset_key))
        except ValueError as exc:
            raise ExperimentDomainError("data_drift", str(exc)) from exc
        if audit.report != revision_row.audit_json:
            raise ExperimentDomainError(
                "data_drift", "dataset files changed after project creation"
            )

        excluded_keys: set[str] = set()
        if project.kind == "formal" and project.pilot_project_id:
            pilot = self._project(project.pilot_project_id)
            excluded_keys = set(
                self.db.scalars(
                    select(ExpInput.input_sha256).where(
                        ExpInput.project_id == pilot.id
                    )
                ).all()
            )
        plan = template.sampling_plan(
            kind=project.kind, audit=audit, excluded_keys=excluded_keys
        )
        selected_keys = [selection.key for selection in plan.selections]
        materialized_items = adapter.materialize(
            self._dataset_root(template.dataset_key), audit, selected_keys
        )
        materialized = {item.key: item for item in materialized_items}
        self._require(
            set(materialized) == set(selected_keys),
            "data_drift",
            "dataset materialization did not return exactly the selected inputs",
        )

        selection_kind = "pilot" if project.kind == "pilot_run" else "formal"
        input_rows: dict[str, ExpInput] = {}
        for selection in plan.selections:
            item = materialized[selection.key]
            input_row = ExpInput(
                project_id=project.id,
                input_sha256=selection.key,
                prompt_sha256=_sha256_text(item.prompt),
                prompt_text=item.prompt,
                essay_text=item.essay,
                word_count=item.word_count,
                stratum=selection.stratum,
                cell_key=selection.cell_key,
                inclusion_probability=selection.inclusion_probability,
                design_weight=selection.design_weight,
                forced=selection.forced,
                selection_kind=selection_kind,
                provenance_json=item.provenance,
            )
            self.db.add(input_row)
            input_rows[selection.key] = input_row
        self.db.flush()
        for key, input_row in input_rows.items():
            for channel, label_x2 in materialized[key].labels_x2.items():
                self.db.add(
                    ExpInputLabel(
                        input_id=input_row.id, channel=channel, label_x2=label_x2
                    )
                )
        self.db.flush()

        bindings = self._bindings(project.id)
        self._require(
            len(bindings) == template.runner_count,
            "conflict",
            "project must have its template's runner bindings",
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
                binding.frozen_runtime_json = self.runner_factory().frozen_runtime(config)
            except RunnerUnavailableError as exc:
                raise ExperimentDomainError("runner_unavailable", str(exc)) from exc

        specs = template.run_group_specs(kind=project.kind)
        spec_run_indexes = [spec.run_index for spec in specs]
        self._require(
            len(set(spec_run_indexes)) == len(spec_run_indexes),
            "conflict",
            "run group specs must have unique run indexes",
        )
        groups: dict[tuple[str, int], ExpRunGroup] = {}
        order = 0
        for spec in specs:
            for binding in bindings:
                order += 1
                group = ExpRunGroup(
                    project_id=project.id,
                    binding_id=binding.id,
                    group_key=spec.group_key,
                    run_index=spec.run_index,
                    status="pending",
                    order_rank=order,
                    expected_calls=0,
                    completed_calls=0,
                )
                self.db.add(group)
                groups[(binding.id, spec.run_index)] = group
        self.db.flush()

        evaluations: list[ExpUniqueEvaluation] = []
        for binding in bindings:
            for key, input_row in input_rows.items():
                run_indexes = (0, 1) if key in plan.retest_keys else (0,)
                for run_index in run_indexes:
                    group = groups[(binding.id, run_index)]
                    evaluation = ExpUniqueEvaluation(
                        project_id=project.id,
                        binding_id=binding.id,
                        run_group_id=group.id,
                        input_id=input_row.id,
                        input_sha256=key,
                        run_index=run_index,
                        status="pending",
                    )
                    evaluations.append(evaluation)
                    self.db.add(evaluation)
                    group.expected_calls += 1
        self.db.flush()

        manifest: dict[str, Any] = {
            "template_id": template.template_id,
            "kind": project.kind,
            "sampling_seed": template.seed,
            "dataset": {
                "key": template.dataset_key,
                "revision_id": revision_row.id,
                "revision_label": revision_row.revision_label,
                "file_sha256": sorted(
                    str(spec["sha256"]) for spec in revision_row.file_specs_json
                ),
            },
            "contract": adapter_contract_for(self.db, revision_row).payload(),
            "rubric_sha256": self._rubric(project.rubric_version_id).rubric_sha256,
            "runners": [
                {
                    "position": binding.position,
                    "model": binding.model,
                    "reasoning_effort": binding.frozen_runtime_json["reasoning_effort"],
                    "speed_mode": binding.frozen_runtime_json["speed_mode"],
                    "service_tier": binding.frozen_runtime_json["service_tier"],
                    "timeout_seconds": binding.frozen_runtime_json["timeout_seconds"],
                    "config_sha256": binding.config_sha256,
                    "cli_version": binding.frozen_runtime_json["cli_version"],
                    "executable_sha256": binding.frozen_runtime_json[
                        "executable_sha256"
                    ],
                }
                for binding in bindings
            ],
            "input_count": len(input_rows),
            "forced_input_count": sum(1 for row in input_rows.values() if row.forced),
            "retest_input_count": len(plan.retest_keys),
            "logical_calls_per_binding": len(evaluations) // max(len(bindings), 1),
            "actual_unique_logical_calls": len(evaluations),
            "groups": [
                {
                    "group_key": spec.group_key,
                    "run_index": spec.run_index,
                }
                for spec in specs
            ],
            "inputs": [
                {
                    "input_sha256": selection.key,
                    "stratum": selection.stratum,
                    "forced": selection.forced,
                    "inclusion_probability": selection.inclusion_probability,
                    "design_weight": selection.design_weight,
                    "retest": selection.key in plan.retest_keys,
                }
                for selection in plan.selections
            ],
        }
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
        self._guard_not_read_only(project)
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
            select(ExpRunGroup).where(ExpRunGroup.project_id == project.id)
        ).all()
        for group in groups:
            group.status = "queued" if group.expected_calls else "completed"
        self._event(project.id, "project_started", {})
        self.db.commit()
        return self.project_out(project)

    def pause_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._guard_not_read_only(project)
        self._require(
            project.status == "running", "conflict", "only a running project can pause"
        )
        project.status = "paused"
        self._event(project.id, "project_paused", {})
        self.db.commit()
        return self.project_out(project)

    def resume_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._guard_not_read_only(project)
        self._require(
            project.status in {"paused", "attention_required"},
            "conflict",
            "only a paused or resolved project can resume",
        )
        failures = self.db.scalar(
            select(func.count())
            .select_from(ExpUniqueEvaluation)
            .where(
                ExpUniqueEvaluation.project_id == project.id,
                ExpUniqueEvaluation.status == "attention_required",
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
        self._guard_not_read_only(project)
        self._require(
            project.status not in {"completed", "terminated"},
            "conflict",
            "project is already terminal",
        )
        project.status = "terminated"
        project.terminated_at = utc_now_naive()
        self.db.execute(
            ExpUniqueEvaluation.__table__.update()
            .where(
                ExpUniqueEvaluation.project_id == project.id,
                ExpUniqueEvaluation.status.in_(("pending", "leased")),
            )
            .values(status="terminated", worker_id=None, lease_until=None)
        )
        self._event(project.id, "project_terminated", {})
        self.db.commit()
        return self.project_out(project)

    def delete_project(self, project_id: str) -> None:
        project = self._project(project_id)
        self._guard_not_read_only(project)
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
            select(ExpRunGroup)
            .where(ExpRunGroup.project_id == project_id)
            .order_by(ExpRunGroup.order_rank)
        ).all()
        return [self.group_out(row) for row in rows]

    def list_calls(self, project_id: str, *, limit: int = 500) -> dict[str, Any]:
        project = self._project(project_id)
        rows = self.db.scalars(
            select(ExpUniqueEvaluation)
            .where(ExpUniqueEvaluation.project_id == project_id)
            .order_by(
                case(
                    (ExpUniqueEvaluation.status == "attention_required", 0),
                    else_=1,
                ),
                ExpUniqueEvaluation.id.desc(),
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
        self._guard_not_read_only(project)
        row = self.db.get(ExpUniqueEvaluation, call_id)
        self._require(
            bool(row and row.project_id == project.id), "not_found", "call not found"
        )
        assert row is not None
        self._require(
            row.status == "attention_required",
            "conflict",
            "only a failed call can be retried",
        )
        group = self.db.get(ExpRunGroup, row.run_group_id)
        self._require(bool(group), "not_found", "run group not found")
        assert group is not None
        row.status = "pending"
        row.failure_code = None
        row.failure_summary = None
        row.worker_id = None
        row.lease_until = None
        group.status = "running"
        project.status = "running"
        self._event(project.id, "call_retried", {"call_id": row.id})
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
        report = self.db.get(ExpLockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        return {"results_embargoed": False, "report": report.report_json}

    def get_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        report = self.db.get(ExpLockedReport, project.id)
        if report is None:
            return {
                "results_embargoed": True,
                "status": project.status,
                "report": None,
            }
        figures = [
            {"name": figure["name"], "sha256": figure["sha256"]}
            for figure in report.figures_json
        ]
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
            "figures": figures,
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
        contract = self._project_contract(project)
        channels = [channel.key for channel in contract.channels]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["template_id", "binding_position", "model", "group_key", "run_index"]
            + ["input_sha256", "stratum", "forced", "inclusion_probability", "weight"]
            + [f"label_{channel}" for channel in channels]
            + [f"prediction_{channel}" for channel in channels]
        )
        records = self._analysis_records(project.id, run_index=None)
        for record in records:
            writer.writerow(
                [
                    project.template_id,
                    record.position,
                    record.model,
                    record.group_key,
                    record.run_index,
                    record.input_sha256,
                    record.stratum,
                    record.forced,
                    record.inclusion_probability,
                    record.design_weight,
                ]
                + [record.labels.get(channel) for channel in channels]
                + [record.predictions.get(channel) for channel in channels]
            )
        payload = output.getvalue()
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def export_report_json(self, project_id: str) -> tuple[str, str]:
        project = self._project(project_id)
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        report = self.db.get(ExpLockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        payload = json.dumps(
            report.report_json, ensure_ascii=False, sort_keys=True, indent=2
        )
        return payload, report.report_sha256

    def export_figures_zip(self, project_id: str) -> tuple[bytes, str]:
        project = self._project(project_id)
        self._require(
            not project.results_embargoed, "embargoed", "formal results remain sealed"
        )
        report = self.db.get(ExpLockedReport, project.id)
        self._require(bool(report), "not_found", "locked report not found")
        assert report is not None
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for figure in report.figures_json:
                archive.writestr(f"{figure['name']}.svg", figure["svg"])
        payload = buffer.getvalue()
        return payload, hashlib.sha256(payload).hexdigest()

    # ----- finalization called by executor -----
    def finalize_if_ready(self, project_id: str) -> bool:
        project = self._project(project_id)
        remaining = int(
            self.db.scalar(
                select(func.count())
                .select_from(ExpUniqueEvaluation)
                .where(
                    ExpUniqueEvaluation.project_id == project.id,
                    ExpUniqueEvaluation.status != "succeeded",
                )
            )
            or 0
        )
        if remaining:
            return False
        groups = self.db.scalars(
            select(ExpRunGroup).where(ExpRunGroup.project_id == project.id)
        ).all()
        now = utc_now_naive()
        for group in groups:
            group.status = "completed"
            group.completed_calls = group.expected_calls
            group.completed_at = group.completed_at or now
        if project.kind == "pilot_run":
            report_json = self._pilot_report(project)
            figures = [
                {
                    "name": "sealed_pilot",
                    "svg": sealed_svg(
                        "技术试点完成",
                        "仅报告格式、失败率与延迟；不显示任何评分结果。",
                    ),
                }
            ]
            self._lock_report(project, report_json, figures, release=False)
        else:
            try:
                report_json, figures = self._formal_report(project)
            except Exception as exc:
                self._event(
                    project.id, "analysis_failed", {"error_code": "analysis_error"}
                )
                self.db.commit()
                raise ExperimentDomainError(
                    "analysis_error", safe_error("analysis_error")
                ) from exc
            self._lock_report(project, report_json, figures, release=True)
        project.status = "completed"
        project.completed_at = now
        self._event(
            project.id, "report_locked", {"report_sha256": project.report_sha256}
        )
        self.db.commit()
        return True

    def _pilot_report(self, project: ExpProject) -> dict[str, Any]:
        attempts = self.db.scalars(
            select(ExpCallAttempt).where(ExpCallAttempt.project_id == project.id)
        ).all()
        latencies = sorted(
            item.latency_ms for item in attempts if item.status == "succeeded"
        )
        counts: dict[str, int] = {}
        for item in attempts:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "template_id": project.template_id,
            "report_type": "technical_pilot",
            "results_embargoed": True,
            "logical_calls": len(attempts),
            "status_counts": dict(sorted(counts.items())),
            "failure_rate": (counts.get("failed", 0) / len(attempts)) if attempts else 0.0,
            "latency_ms": {
                "p50": statistics.median(latencies) if latencies else None,
                "p95": _percentile(latencies, 0.95),
            },
            "disclosure": "技术试点不显示分数、趋势或效应，也不用于确认性推断。",
        }

    def _formal_report(
        self, project: ExpProject
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        template = get_template(project.template_id)
        rows = self._analysis_rows(project.id, run_index=0)
        retest_rows = self._analysis_rows(project.id, run_index=1)
        attempts = [
            {
                "evaluation_id": item.evaluation_id,
                "attempt_number": item.attempt_number,
                "status": item.status,
                "latency_ms": item.latency_ms,
                "error_code": item.error_code,
                "started_at": item.started_at.isoformat(),
            }
            for item in self.db.scalars(
                select(ExpCallAttempt).where(ExpCallAttempt.project_id == project.id)
            ).all()
        ]
        report, figures = template.build_report(
            project=self.project_out(project),
            rows=rows,
            retest_rows=retest_rows,
            attempts=attempts,
        )
        report["template_id"] = project.template_id
        report["manifest_sha256"] = project.manifest_sha256
        report["report_type"] = "formal"
        return report, figures

    def _analysis_rows(self, project_id: str, *, run_index: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self._analysis_records(project_id, run_index=run_index):
            rows.append(
                {
                    "binding_id": record.binding_id,
                    "position": record.position,
                    "model": record.model,
                    "group_key": record.group_key,
                    "run_index": record.run_index,
                    "input_sha256": record.input_sha256,
                    "prompt_sha256": record.prompt_sha256,
                    "word_count": record.word_count,
                    "stratum": record.stratum,
                    "forced": record.forced,
                    "inclusion_probability": record.inclusion_probability,
                    "weight": record.design_weight,
                    "labels_x2": record.labels,
                    "predictions_x2": record.predictions,
                }
            )
        return rows

    def _analysis_records(
        self, project_id: str, *, run_index: int | None
    ) -> list[Any]:
        """Joined, hash-only records for exports and analysis (no text)."""

        class _Record:
            pass

        query = (
            select(
                ExpUniqueEvaluation,
                ExpInput,
                ExpProjectRunner,
                ExpRunGroup.group_key,
            )
            .join(ExpInput, ExpInput.id == ExpUniqueEvaluation.input_id)
            .join(
                ExpProjectRunner,
                ExpProjectRunner.id == ExpUniqueEvaluation.binding_id,
            )
            .join(ExpRunGroup, ExpRunGroup.id == ExpUniqueEvaluation.run_group_id)
            .where(
                ExpUniqueEvaluation.project_id == project_id,
                ExpUniqueEvaluation.status == "succeeded",
            )
            .order_by(ExpUniqueEvaluation.id)
        )
        if run_index is not None:
            query = query.where(ExpUniqueEvaluation.run_index == run_index)

        labels_by_input: dict[int, dict[str, int]] = {}
        label_rows = self.db.execute(
            select(
                ExpInputLabel.input_id, ExpInputLabel.channel, ExpInputLabel.label_x2
            )
            .join(ExpInput, ExpInput.id == ExpInputLabel.input_id)
            .where(ExpInput.project_id == project_id)
        ).all()
        for input_id, channel, label_x2 in label_rows:
            labels_by_input.setdefault(input_id, {})[channel] = label_x2

        predictions_by_evaluation: dict[int, dict[str, int]] = {}
        score_rows = self.db.execute(
            select(
                ExpCallScore.evaluation_id,
                ExpCallScore.channel,
                ExpCallScore.score_x2,
            )
            .join(
                ExpUniqueEvaluation,
                ExpUniqueEvaluation.id == ExpCallScore.evaluation_id,
            )
            .where(ExpUniqueEvaluation.project_id == project_id)
        ).all()
        for evaluation_id, channel, score_x2 in score_rows:
            predictions_by_evaluation.setdefault(evaluation_id, {})[channel] = score_x2

        records: list[Any] = []
        for evaluation, input_row, binding, group_key in self.db.execute(query).all():
            record = _Record()
            record.binding_id = evaluation.binding_id
            record.position = binding.position
            record.model = binding.model
            record.group_key = group_key
            record.run_index = evaluation.run_index
            record.input_sha256 = evaluation.input_sha256
            record.prompt_sha256 = input_row.prompt_sha256
            record.word_count = input_row.word_count
            record.stratum = input_row.stratum
            record.forced = input_row.forced
            record.inclusion_probability = input_row.inclusion_probability
            record.design_weight = input_row.design_weight
            record.labels = dict(labels_by_input.get(input_row.id, {}))
            record.predictions = dict(predictions_by_evaluation.get(evaluation.id, {}))
            records.append(record)
        return records

    def _lock_report(
        self,
        project: ExpProject,
        report_json: dict,
        figures: list[dict[str, str]],
        *,
        release: bool,
    ) -> None:
        report_hash = canonical_sha256(report_json)
        for figure in figures:
            figure["sha256"] = hashlib.sha256(figure["svg"].encode("utf-8")).hexdigest()
        self.db.add(
            ExpLockedReport(
                project_id=project.id,
                report_json=report_json,
                report_sha256=report_hash,
                figures_json=figures,
            )
        )
        project.report_sha256 = report_hash
        project.results_embargoed = not release

    # ----- serializers and lookup -----
    @staticmethod
    def runner_out(row: ExpRunnerConfig) -> dict[str, Any]:
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
    def rubric_out(row: ExpRubricVersion) -> dict[str, Any]:
        return {
            "id": row.id,
            "template_id": row.template_id,
            "name": row.name,
            "rubric": row.rubric_text,
            "rubric_sha256": row.rubric_sha256,
            "status": row.status,
            "created_at": row.created_at,
        }

    def project_out(self, row: ExpProject) -> dict[str, Any]:
        counts = dict(
            self.db.execute(
                select(ExpUniqueEvaluation.status, func.count())
                .where(ExpUniqueEvaluation.project_id == row.id)
                .group_by(ExpUniqueEvaluation.status)
            ).all()
        )
        bindings = self._bindings(row.id)
        total = sum(counts.values())
        latencies = self.db.scalars(
            select(ExpUniqueEvaluation.latency_ms).where(
                ExpUniqueEvaluation.project_id == row.id,
                ExpUniqueEvaluation.latency_ms.is_not(None),
            )
        ).all()
        return {
            "id": row.id,
            "template_id": row.template_id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "dataset_revision_id": row.dataset_revision_id,
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
            "read_only": row.read_only,
            "source_system": row.source_system,
            "source_project_id": row.source_project_id,
            "data_processing_confirmed": row.data_processing_confirmed,
            "data_status": row.data_status_json,
            "manifest_sha256": row.manifest_sha256,
            "manifest_summary": {
                key: value
                for key, value in row.manifest_json.items()
                if key not in {"inputs"}
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
    def group_out(row: ExpRunGroup) -> dict[str, Any]:
        return {
            "id": row.id,
            "binding_id": row.binding_id,
            "group_key": row.group_key,
            "run_index": row.run_index,
            "status": row.status,
            "order_rank": row.order_rank,
            "expected_calls": row.expected_calls,
            "completed_calls": row.completed_calls,
        }

    def call_out(self, row: ExpUniqueEvaluation, *, embargoed: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": row.id,
            "binding_id": row.binding_id,
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
                safe_error(row.failure_code)
                if row.failure_code is not None
                else row.failure_summary
            ),
        }
        if not embargoed:
            scores = self.db.execute(
                select(ExpCallScore.channel, ExpCallScore.score_x2).where(
                    ExpCallScore.evaluation_id == row.id
                )
            ).all()
            value["scores"] = {channel: score_x2 for channel, score_x2 in scores}
        return value

    def _project_contract(self, project: ExpProject) -> ScoringContract:
        assert project.dataset_revision_id is not None
        return self.revision_contract(project.dataset_revision_id)

    def _runner(self, config_id: str) -> ExpRunnerConfig:
        row = self.db.get(ExpRunnerConfig, config_id)
        self._require(bool(row), "not_found", "runner not found")
        assert row is not None
        return row

    def _rubric(self, rubric_id: str | None) -> ExpRubricVersion:
        self._require(bool(rubric_id), "not_found", "rubric not found")
        assert rubric_id is not None
        row = self.db.get(ExpRubricVersion, rubric_id)
        self._require(bool(row), "not_found", "rubric not found")
        assert row is not None
        return row

    def _project(self, project_id: str) -> ExpProject:
        row = self.db.get(ExpProject, project_id)
        self._require(bool(row), "not_found", "project not found")
        assert row is not None
        return row

    def _bindings(self, project_id: str) -> list[ExpProjectRunner]:
        return list(
            self.db.scalars(
                select(ExpProjectRunner)
                .where(ExpProjectRunner.project_id == project_id)
                .order_by(ExpProjectRunner.position)
            ).all()
        )

    def _event(self, project_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self.db.add(
            ExpEvent(project_id=project_id, event_type=event_type, detail_json=detail)
        )

    @staticmethod
    def _guard_not_read_only(project: ExpProject) -> None:
        ExperimentService._require(
            not project.read_only,
            "forbidden",
            "legacy read-only projects cannot be mutated",
        )

    @staticmethod
    def _require(condition: bool, code: str, message: str) -> None:
        if not condition:
            raise ExperimentDomainError(code, message)


def adapter_contract_for(db: Session, revision: ExpDatasetRevision) -> ScoringContract:
    row = db.scalar(
        select(ExpScoringContract).where(
            ExpScoringContract.dataset_revision_id == revision.id
        )
    )
    if row is not None:
        return contract_from_payload(
            {
                "channels": row.channels_json,
                "grid_min_x2": row.grid_min_x2,
                "grid_max_x2": row.grid_max_x2,
            }
        )
    from app.experiment.core.registry import get_dataset

    dataset = db.get(ExpDataset, revision.dataset_id)
    assert dataset is not None
    return get_dataset(dataset.key).contract()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def sealed_svg(title: str, message: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="360" viewBox="0 0 960 360" role="img" '
        f'aria-label="{title}"><rect width="960" height="360" fill="#f8fafc"/>'
        f'<text x="480" y="180" text-anchor="middle" font-size="28" fill="#1e293b">{title}</text>'
        f'<text x="480" y="215" text-anchor="middle" font-size="16" fill="#64748b">{message}</text></svg>'
    )


# Template registration: importing the concrete templates populates the
# registry before any service call needs it.
from app.experiment import templates as _templates  # noqa: E402,F401

__all__ = [
    "ExperimentDomainError",
    "ExperimentService",
    "ProjectIn",
    "RubricIn",
    "RunnerConfigIn",
    "canonical_sha256",
    "safe_error",
    "sealed_svg",
]
