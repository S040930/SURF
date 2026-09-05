"""One-call r23 worker step shared by MCP and synthetic end-to-end tests."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.experiment.r21.codex_runner import (
    CodexExecRunner,
    RunnerDriftError,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.experiment.r23.protocol import R23Score, scoring_messages
from app.models.r23 import (
    R23CallAttempt,
    R23ModelBinding,
    R23Project,
    R23RubricVersion,
    R23RunGroup,
    R23UniqueEvaluation,
)
from app.services.r23 import R23DomainError, R23Service

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
        if message == "r23 score value validation failed":
            return "invalid_score_value"
        if message in {
            "codex exec did not write a structured result",
            "codex structured result exceeded size limit",
        } or message.startswith("codex structured result failed validation:"):
            return "invalid_score_schema"
        return "execution_error"
    return "execution_error"


def _safe_summary(code: str) -> str:
    return {
        "interrupted": "MCP connection ended during the call",
        "runner_drift": "run-snapshot Codex CLI fingerprint changed",
        "runner_unavailable": "Codex CLI is unavailable",
        "timeout": "Codex call exceeded the configured timeout",
        # Historical records can retain this legacy code, but new failures use
        # the two categories below. Never expose the old generic English text.
        "invalid_output": (
            "模型未返回仅含 content、organization、language 三字段的有效 JSON 分数对象"
        ),
        "invalid_score_schema": (
            "模型未返回仅含 content、organization、language 三字段的有效 JSON 分数对象"
        ),
        "invalid_score_value": "模型返回的分数必须为 1–5，且间隔为 0.5",
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
        select(R23UniqueEvaluation)
        .join(R23Project, R23Project.id == R23UniqueEvaluation.project_id)
        .join(R23RunGroup, R23RunGroup.id == R23UniqueEvaluation.run_group_id)
        .where(
            R23Project.status == "running",
            R23UniqueEvaluation.status == "pending",
        )
        .order_by(R23RunGroup.order_rank, R23UniqueEvaluation.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    evaluation = db.scalar(query)
    if evaluation is None:
        return False

    project = db.get(R23Project, evaluation.project_id)
    binding = db.get(R23ModelBinding, evaluation.model_binding_id)
    group = db.get(R23RunGroup, evaluation.run_group_id)
    if project is None or binding is None or group is None:
        raise RuntimeError("r23 evaluation references missing run-snapshot records")
    rubric = db.get(R23RubricVersion, project.rubric_version_id)
    if rubric is None:
        raise RuntimeError("r23 project rubric is missing")

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
            messages=scoring_messages(
                rubric=rubric.rubric_text,
                prompt=evaluation.prompt_text,
                essay=evaluation.essay_text,
            ),
            schema=R23Score,
            runtime=binding.frozen_runtime_json,
        )
        try:
            score = R23Score.model_validate(result.value)
        except Exception as exc:
            raise RunnerExecutionError("r23 score value validation failed") from exc
        score_x2 = score.as_x2()
        evaluation = db.get(R23UniqueEvaluation, evaluation.id)
        assert evaluation is not None
        evaluation.content_score_x2 = score_x2["content"]
        evaluation.organization_score_x2 = score_x2["organization"]
        evaluation.language_score_x2 = score_x2["language"]
        evaluation.status = "succeeded"
        evaluation.latency_ms = result.latency_ms
        evaluation.failure_code = None
        evaluation.failure_summary = None
        evaluation.worker_id = None
        evaluation.lease_until = None
        evaluation.completed_at = utc_now_naive()
        output_sha = hashlib.sha256(
            json.dumps(
                score.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        db.add(
            R23CallAttempt(
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
        group = db.get(R23RunGroup, evaluation.run_group_id)
        assert group is not None
        group.completed_calls += 1
        if group.completed_calls >= group.expected_calls:
            group.status = "completed"
            group.completed_at = utc_now_naive()
        db.commit()
        R23Service(db, runner_factory=runner_factory).finalize_if_ready(project.id)
        return True
    except (RunnerExecutionError, RunnerUnavailableError, RunnerDriftError) as exc:
        code = _failure_code(exc)
        evaluation = db.get(R23UniqueEvaluation, evaluation.id)
        project = db.get(R23Project, project.id)
        group = db.get(R23RunGroup, group.id)
        assert evaluation is not None and project is not None and group is not None
        evaluation.status = "attention_required"
        evaluation.failure_code = code
        evaluation.failure_summary = _safe_summary(code)
        evaluation.worker_id = None
        evaluation.lease_until = None
        group.status = "blocked"
        project.status = "attention_required"
        db.add(
            R23CallAttempt(
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
                error_summary=_safe_summary(code),
                started_at=started_at,
            )
        )
        db.commit()
        return True
    except R23DomainError:
        raise


__all__ = ["run_one"]
