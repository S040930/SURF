"""One-call unified-core worker step shared by MCP and synthetic E2E tests."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.experiment.core.contracts import ContractError, contract_from_payload
from app.experiment.r21.codex_runner import (
    CodexExecRunner,
    RunnerDriftError,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.models.experiments import (
    ExpCallAttempt,
    ExpCallScore,
    ExpDatasetRevision,
    ExpInput,
    ExpProject,
    ExpProjectRunner,
    ExpRubricVersion,
    ExpRunGroup,
    ExpScoringContract,
    ExpUniqueEvaluation,
)
from app.services.experiments import ExperimentDomainError, ExperimentService

LEASE_SECONDS = 660


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, RunnerInterruptedError):
        return "interrupted"
    if isinstance(exc, RunnerDriftError):
        return "runner_drift"
    if isinstance(exc, RunnerUnavailableError):
        return "runner_unavailable"
    if isinstance(exc, RunnerExecutionError):
        message = str(exc)
        if message == "codex exec timed out":
            return "timeout"
        if message == "experiment score value validation failed":
            return "invalid_score_value"
        if message in {
            "codex exec did not write a structured result",
            "codex structured result exceeded size limit",
        } or message.startswith("codex structured result failed validation:"):
            return "invalid_score_schema"
        return "execution_error"
    return "execution_error"


def _safe_summary(code: str, channel_keys: list[str], grid_text: str) -> str:
    return {
        "interrupted": "MCP connection ended during the call",
        "runner_drift": "run-snapshot Codex CLI fingerprint changed",
        "runner_unavailable": "Codex CLI is unavailable",
        "timeout": "Codex call exceeded the configured timeout",
        "invalid_score_schema": (
            "模型未返回仅含 "
            + "、".join(channel_keys)
            + " 字段的有效 JSON 分数对象"
        ),
        "invalid_score_value": f"模型返回的分数必须为 {grid_text} 网格上的分值",
        "execution_error": "Codex call did not complete successfully",
    }[code]


def run_one(
    db: Session,
    *,
    worker_id: str,
    runner_factory: Callable[[], CodexExecRunner] = CodexExecRunner,
) -> bool:
    """Claim and execute one logical call. Failures always require manual retry."""
    query = (
        select(ExpUniqueEvaluation)
        .join(ExpProject, ExpProject.id == ExpUniqueEvaluation.project_id)
        .join(ExpRunGroup, ExpRunGroup.id == ExpUniqueEvaluation.run_group_id)
        .where(
            ExpProject.status == "running",
            ExpUniqueEvaluation.status == "pending",
        )
        .order_by(ExpRunGroup.order_rank, ExpUniqueEvaluation.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    evaluation = db.scalar(query)
    if evaluation is None:
        return False

    project = db.get(ExpProject, evaluation.project_id)
    binding = db.get(ExpProjectRunner, evaluation.binding_id)
    group = db.get(ExpRunGroup, evaluation.run_group_id)
    if project is None or binding is None or group is None:
        raise RuntimeError("experiment evaluation references missing run-snapshot records")
    rubric = db.get(ExpRubricVersion, project.rubric_version_id)
    if rubric is None:
        raise RuntimeError("experiment project rubric is missing")
    revision = db.get(ExpDatasetRevision, project.dataset_revision_id)
    contract_row = None
    if revision is not None:
        contract_row = db.scalar(
            select(ExpScoringContract).where(
                ExpScoringContract.dataset_revision_id == revision.id
            )
        )
    if contract_row is None:
        raise RuntimeError("experiment project scoring contract is missing")
    contract = contract_from_payload(
        {
            "channels": contract_row.channels_json,
            "grid_min_x2": contract_row.grid_min_x2,
            "grid_max_x2": contract_row.grid_max_x2,
        }
    )
    input_row = db.get(ExpInput, evaluation.input_id)
    if input_row is None:
        raise RuntimeError("experiment evaluation input is missing")
    channel_keys = [channel["key"] for channel in contract_row.channels_json]
    grid_text = f"{contract.grid_min_x2 / 2:g}–{contract.grid_max_x2 / 2:g}"

    evaluation.status = "leased"
    evaluation.worker_id = worker_id
    evaluation.lease_until = utc_now_naive() + timedelta(seconds=LEASE_SECONDS)
    evaluation.attempt_count += 1
    group.status = "running"
    group.started_at = group.started_at or utc_now_naive()
    attempt_number = evaluation.attempt_count
    started_at = utc_now_naive()
    db.commit()

    runner: CodexExecRunner | None = None
    try:
        runner = runner_factory()
        runner.assert_matches(binding.frozen_runtime_json)
        result = runner.run(
            messages=contract.scoring_messages(
                rubric=rubric.rubric_text,
                prompt=input_row.prompt_text,
                essay=input_row.essay_text,
            ),
            schema=contract.score_model,
            runtime=binding.frozen_runtime_json,
        )
        try:
            score_x2 = contract.validate_scores(result.value)
        except ContractError as exc:
            raise RunnerExecutionError(
                "experiment score value validation failed"
            ) from exc
        evaluation = db.get(ExpUniqueEvaluation, evaluation.id)
        assert evaluation is not None
        for channel, value_x2 in score_x2.items():
            db.add(
                ExpCallScore(
                    evaluation_id=evaluation.id, channel=channel, score_x2=value_x2
                )
            )
        evaluation.status = "succeeded"
        evaluation.latency_ms = result.latency_ms
        evaluation.failure_code = None
        evaluation.failure_summary = None
        evaluation.worker_id = None
        evaluation.lease_until = None
        evaluation.completed_at = utc_now_naive()
        output_sha = hashlib.sha256(
            json.dumps(
                result.value, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        db.add(
            ExpCallAttempt(
                project_id=project.id,
                evaluation_id=evaluation.id,
                attempt_number=attempt_number,
                input_sha256=evaluation.input_sha256,
                requested_model=binding.model,
                reasoning_effort=str(binding.frozen_runtime_json["reasoning_effort"]),
                speed_mode=str(binding.frozen_runtime_json["speed_mode"]),
                timeout_seconds=int(binding.frozen_runtime_json["timeout_seconds"]),
                status="succeeded",
                latency_ms=result.latency_ms,
                exit_code=result.exit_code,
                output_sha256=output_sha,
                started_at=started_at,
            )
        )
        group = db.get(ExpRunGroup, evaluation.run_group_id)
        assert group is not None
        group.completed_calls += 1
        if group.completed_calls >= group.expected_calls:
            group.status = "completed"
            group.completed_at = utc_now_naive()
        db.commit()
        ExperimentService(db, runner_factory=runner_factory).finalize_if_ready(
            project.id
        )
        return True
    except (RunnerExecutionError, RunnerUnavailableError, RunnerDriftError) as exc:
        code = _failure_code(exc)
        evaluation = db.get(ExpUniqueEvaluation, evaluation.id)
        project = db.get(ExpProject, project.id)
        group = db.get(ExpRunGroup, group.id)
        assert evaluation is not None and project is not None and group is not None
        evaluation.status = "attention_required"
        evaluation.failure_code = code
        evaluation.failure_summary = _safe_summary(code, channel_keys, grid_text)
        evaluation.worker_id = None
        evaluation.lease_until = None
        group.status = "blocked"
        project.status = "attention_required"
        db.add(
            ExpCallAttempt(
                project_id=project.id,
                evaluation_id=evaluation.id,
                attempt_number=attempt_number,
                input_sha256=evaluation.input_sha256,
                requested_model=binding.model,
                reasoning_effort=str(binding.frozen_runtime_json["reasoning_effort"]),
                speed_mode=str(binding.frozen_runtime_json["speed_mode"]),
                timeout_seconds=int(binding.frozen_runtime_json["timeout_seconds"]),
                status="failed",
                latency_ms=0,
                exit_code=getattr(exc, "exit_code", None),
                error_code=code,
                error_summary=_safe_summary(code, channel_keys, grid_text),
                started_at=started_at,
            )
        )
        db.commit()
        return True
    except ExperimentDomainError:
        raise


__all__ = ["LEASE_SECONDS", "run_one"]
