"""Serial r21 execution engine backed by :mod:`codex_runner`."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.experiment.r20.analysis import compute_analysis
from app.experiment.r20.runner import score_messages, update_messages
from app.experiment.r21.codex_runner import (
    CodexExecRunner,
    RunnerDriftError,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.experiment.r21.memory import parse_memory_update, render_memory
from app.experiment.r21.protocol import (
    FINAL_REPEATS,
    PromptTemplates,
    output_schema,
    schedule_from_manifest,
)
from app.experiment.r22 import PROTOCOL_ID as R22_PROTOCOL_ID
from app.experiment.r22.analysis import compute_analysis as compute_r22_analysis
from app.experiment.r22.executor import call_sequence as r22_call_sequence
from app.experiment.r22.recovery import (
    TerminalRecoveryError,
    audit_payload,
    terminal_audit_payload,
)
from app.experiment.r22.runner import run_with_recovery
from app.models.r21 import (
    R21Call,
    R21CallAttempt,
    R21Exposure,
    R21Project,
    R21Record,
    R21Report,
    R21RunGroup,
    R21Snapshot,
    R21Stream,
)
from app.models.r22 import R22CompressionAudit


@dataclass(frozen=True, slots=True)
class CallSpec:
    kind: str
    question_id: str
    condition: str
    trajectory: int
    answer_id: str
    history_count: int
    repeat: int


def _records(
    db: Session, project_id: str, question: str, usage: str, trajectory: int = 0
) -> list[R21Record]:
    statement = select(R21Record).where(
        R21Record.project_id == project_id,
        R21Record.question_id == question,
        R21Record.usage == usage,
    )
    if usage == "memory":
        statement = statement.where(R21Record.trajectory == trajectory)
    return list(db.scalars(statement.order_by(R21Record.position)))


def call_sequence(
    db: Session, project: R21Project, stream: R21Stream
) -> list[CallSpec]:
    if project.protocol_id == R22_PROTOCOL_ID:
        memory = _records(
            db, project.id, stream.question_id, "memory", stream.trajectory
        )
        tests = _records(db, project.id, stream.question_id, "test")
        return [
            CallSpec(
                spec.kind,
                spec.question_id,
                spec.condition,
                spec.trajectory,
                spec.answer_id,
                spec.history_count,
                spec.repeat,
            )
            for spec in r22_call_sequence(
                stream.question_id,
                stream.condition,
                stream.trajectory,
                [row.answer_id for row in memory],
                [row.answer_id for row in tests],
            )
        ]
    schedule = schedule_from_manifest(project.manifest_json, project.kind)
    memory, tests = _records(
        db, project.id, stream.question_id, "memory", stream.trajectory
    ), _records(db, project.id, stream.question_id, "test")
    probes = [row for row in tests if row.probe]
    specs: list[CallSpec] = []
    if stream.condition != "nm":
        for position, row in enumerate(memory, 1):
            specs.append(
                CallSpec(
                    "memory_update",
                    stream.question_id,
                    stream.condition,
                    stream.trajectory,
                    row.answer_id,
                    position,
                    0,
                )
            )
            if position in schedule.probe_checkpoints:
                specs.extend(
                    CallSpec(
                        "test_score",
                        stream.question_id,
                        stream.condition,
                        stream.trajectory,
                        row.answer_id,
                        position,
                        1,
                    )
                    for row in probes
                )
    else:
        for history in schedule.probe_checkpoints:
            specs.extend(
                CallSpec(
                    "test_score",
                    stream.question_id,
                    "nm",
                    stream.trajectory,
                    row.answer_id,
                    history,
                    1,
                )
                for row in probes
            )
    specs.extend(
        CallSpec(
            "test_score",
            stream.question_id,
            stream.condition,
            stream.trajectory,
            row.answer_id,
            schedule.final_history,
            repeat,
        )
        for row in tests
        for repeat in FINAL_REPEATS
    )
    return specs


def _existing(db: Session, project_id: str, spec: CallSpec) -> R21Call | None:
    return db.scalar(
        select(R21Call).where(
            R21Call.project_id == project_id,
            R21Call.question_id == spec.question_id,
            R21Call.condition == spec.condition,
            R21Call.trajectory == spec.trajectory,
            R21Call.kind == spec.kind,
            R21Call.answer_id == spec.answer_id,
            R21Call.history_count == spec.history_count,
            R21Call.repeat == spec.repeat,
        )
    )


def _record(db: Session, project: R21Project, spec: CallSpec) -> R21Record:
    usage = "memory" if spec.kind in {"memory_update", "train_score"} else "test"
    statement = select(R21Record).where(
        R21Record.project_id == project.id,
        R21Record.question_id == spec.question_id,
        R21Record.answer_id == spec.answer_id,
        R21Record.usage == usage,
    )
    if usage == "memory":
        statement = statement.where(R21Record.trajectory == spec.trajectory)
    row = db.scalar(statement)
    if row is None:
        raise RuntimeError(f"r21 record is missing: {spec.answer_id}")
    return row


def _snapshot(
    db: Session, project: R21Project, spec: CallSpec, history: int
) -> R21Snapshot | None:
    return db.scalar(
        select(R21Snapshot).where(
            R21Snapshot.project_id == project.id,
            R21Snapshot.question_id == spec.question_id,
            R21Snapshot.condition == spec.condition,
            R21Snapshot.trajectory == spec.trajectory,
            R21Snapshot.history_count == history,
        )
    )


def next_call(db: Session, project: R21Project, stream: R21Stream) -> CallSpec | None:
    for spec in call_sequence(db, project, stream):
        current = _existing(db, project.id, spec)
        if current and current.status == "failed_terminal":
            return None
        if current and current.status == "succeeded":
            continue
        if (
            spec.kind == "test_score"
            and spec.condition != "nm"
            and not _snapshot(db, project, spec, spec.history_count)
        ):
            return None
        return spec
    return None


def _input_and_messages(
    db: Session, project: R21Project, spec: CallSpec
) -> tuple[dict[str, Any], tuple[dict[str, str], ...], R21Record, R21Snapshot | None]:
    templates, row = PromptTemplates.model_validate(
        project.prompt_templates_json
    ), _record(db, project, spec)
    if spec.kind in {"test_score", "train_score"}:
        snapshot = (
            None
            if spec.condition == "nm"
            else _snapshot(db, project, spec, spec.history_count)
        )
        items = list(snapshot.items_json) if snapshot else []
        memory = (
            render_memory(spec.condition, items)
            if items
            else "No learned memory is available for this question."
        )
        return (
            {
                "question": row.question_text,
                "reference_answer": row.reference_answer,
                "answer": row.student_answer,
                "max_score": row.max_score,
                "memory": memory,
            },
            score_messages(
                templates.scoring,
                row.question_text,
                row.reference_answer,
                row.student_answer,
                row.max_score,
                memory,
            ),
            row,
            snapshot,
        )
    prior = _snapshot(db, project, spec, spec.history_count - 1)
    items = list(prior.items_json) if prior else []
    instruction = (
        templates.crm_update if spec.condition == "crm" else templates.arm_update
    )
    return (
        {
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "max_score": row.max_score,
            "teacher_score": row.teacher_score,
            "teacher_feedback": row.teacher_feedback,
            "existing_memory": items,
        },
        update_messages(
            instruction,
            row.question_text,
            row.reference_answer,
            row.student_answer,
            row.max_score,
            row.teacher_score,
            row.teacher_feedback,
            items,
        ),
        row,
        prior,
    )


def _complete_stream(db: Session, project: R21Project, stream: R21Stream) -> None:
    stream.status, stream.worker_id, stream.lease_until = "completed", None, None
    group = db.scalar(
        select(R21RunGroup).where(
            R21RunGroup.project_id == project.id,
            R21RunGroup.question_id == stream.question_id,
            R21RunGroup.condition == stream.condition,
        )
    )
    group_completed = bool(group) and not db.scalar(
        select(R21Stream.id).where(
            R21Stream.project_id == project.id,
            R21Stream.question_id == stream.question_id,
            R21Stream.condition == stream.condition,
            R21Stream.status != "completed",
        )
    )
    if group_completed and group:
        group.status, group.completed_at = "completed", utc_now_naive()
    db.commit()
    if not group_completed:
        return
    db.refresh(project)
    if project.status == "running":
        next_group = db.scalar(
            select(R21RunGroup)
            .where(
                R21RunGroup.project_id == project.id,
                R21RunGroup.status == "pending",
            )
            .order_by(R21RunGroup.order_rank)
            .limit(1)
        )
        if next_group:
            next_group.status = "queued"
            next_group.started_at = utc_now_naive()
            db.commit()
            return
    incomplete = db.scalar(
        select(R21RunGroup.id).where(
            R21RunGroup.project_id == project.id,
            R21RunGroup.status != "completed",
        )
    )
    if incomplete is None and project.status not in {"terminated", "paused"}:
        failed = db.scalar(
            select(R21Call.id).where(
                R21Call.project_id == project.id,
                R21Call.status != "succeeded",
            )
        )
        project.status = "completed_with_failures" if failed else "completed"
        project.completed_at = utc_now_naive()
        db.commit()
        _lock_report(db, project)


def _lock_report(db: Session, project: R21Project) -> None:
    """Lock the r21 report with one frozen model, never a by-model aggregate."""
    if db.get(R21Report, project.id) is not None:
        return
    calls = list(
        db.scalars(
            select(R21Call).where(R21Call.project_id == project.id).order_by(R21Call.id)
        )
    )
    attempts = list(
        db.scalars(
            select(R21CallAttempt).join(R21Call).where(R21Call.project_id == project.id)
        )
    )
    failures = [
        {"call_id": row.id, "reason": row.failure_reason}
        for row in calls
        if row.status != "succeeded"
    ]
    resources = {
        "attempts": len(attempts),
        "latency_ms": sum(row.latency_ms for row in attempts),
        "latencies_ms": [row.latency_ms for row in attempts],
        "failed_calls": len(failures),
        "model": project.runner_runtime_json["model"],
        "reasoning_effort": project.runner_runtime_json["reasoning_effort"],
        "cli_version": project.runner_runtime_json["cli_version"],
    }
    if project.protocol_id == R22_PROTOCOL_ID:
        rows = [
            {
                "question_id": row.question_id,
                "condition": row.condition,
                "trajectory": row.trajectory,
                "kind": row.kind,
                "answer_id": row.answer_id,
                "history_count": row.history_count,
                "repeat": row.repeat,
                "model_score": row.model_score,
                "teacher_score": row.teacher_score,
                "max_score": row.max_score,
            }
            for row in calls
            if row.status == "succeeded"
        ]
        audits = list(
            db.scalars(
                select(R22CompressionAudit).where(
                    R22CompressionAudit.project_id == project.id
                )
            )
        )
        report = compute_r22_analysis(
            rows,
            [row for row in rows if row["kind"] == "test_score"],
            compression_audits=audits,
            resources=resources,
        )
        report["analysis_status"] = "r22_pilot_diagnostic"
    elif project.kind == "pilot_run":
        report = {
            "core": None,
            "supplement": {"resources": resources, "failures": failures},
            "analysis_status": "pilot_technical_only",
        }
    elif failures:
        report = {
            "core": None,
            "supplement": {"resources": resources, "failures": failures},
            "analysis_status": "incomplete_due_to_terminal_failures",
        }
    else:
        rows = [
            {
                "model_id": project.runner_runtime_json["model"],
                "question_id": row.question_id,
                "group_id": row.group_id,
                "entry_id": row.answer_id,
                "condition": row.condition,
                "trajectory": row.trajectory,
                "history_count": row.history_count,
                "repeat": row.repeat,
                "model_score": row.model_score,
                "teacher_score": row.teacher_score,
                "max_score": row.max_score,
            }
            for row in calls
            if row.kind == "test_score" and row.status == "succeeded"
        ]
        schedule = schedule_from_manifest(project.manifest_json, project.kind)
        report = compute_analysis(
            rows, resources, failures, final_history=schedule.final_history
        )
        report["core"].pop("by_model", None)
        report["scope"] = {
            "protocol": project.protocol_id,
            "model": project.runner_runtime_json["model"],
            "reasoning_effort": project.runner_runtime_json["reasoning_effort"],
            "cli_version": project.runner_runtime_json["cli_version"],
            "agent_harness": "codex-exec-ephemeral",
        }
    digest = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    db.add(R21Report(project_id=project.id, report_json=report, report_sha256=digest))
    project.report_sha256 = digest
    db.commit()


def run_one(db: Session, *, worker_id: str, runner_factory=CodexExecRunner) -> bool:
    """Run at most one logical call.  Returns false when nothing is runnable."""
    group = db.scalar(
        select(R21RunGroup)
        .join(R21Project)
        .where(
            R21Project.status == "running",
            R21RunGroup.status.in_(("queued", "running")),
        )
        .order_by(R21Project.started_at, R21RunGroup.order_rank)
        .limit(1)
    )
    if group is None:
        return False
    project = db.get(R21Project, group.project_id)
    if project is None:
        return False
    if group.status == "queued":
        group.status = "running"
        db.commit()
    stream = db.scalar(
        select(R21Stream)
        .where(
            R21Stream.project_id == project.id,
            R21Stream.question_id == group.question_id,
            R21Stream.condition == group.condition,
            R21Stream.status == "pending",
        )
        .order_by(R21Stream.order_rank)
        .limit(1)
    )
    if stream is None:
        # A prior iteration may have completed the final stream while the
        # group transition was interrupted. Repair that boundary here so a
        # worker restart/reconnect can continue without manual intervention.
        remaining_stream = db.scalar(
            select(R21Stream.id).where(
                R21Stream.project_id == project.id,
                R21Stream.question_id == group.question_id,
                R21Stream.condition == group.condition,
                R21Stream.status != "completed",
            )
        )
        if remaining_stream is None and group.status != "completed":
            group.status, group.completed_at = "completed", utc_now_naive()
            db.commit()
            if project.status == "running":
                next_group = db.scalar(
                    select(R21RunGroup)
                    .where(
                        R21RunGroup.project_id == project.id,
                        R21RunGroup.status == "pending",
                    )
                    .order_by(R21RunGroup.order_rank)
                    .limit(1)
                )
                if next_group:
                    next_group.status = "queued"
                    next_group.started_at = utc_now_naive()
                    db.commit()
            return True
        return False
    stream.status, stream.worker_id = "leased", worker_id
    db.commit()
    spec = next_call(db, project, stream)
    if spec is None:
        # Either finished or blocked by a terminal failure.  Do not advance past a failure.
        failed = db.scalar(
            select(R21Call.id).where(
                R21Call.project_id == project.id,
                R21Call.question_id == stream.question_id,
                R21Call.condition == stream.condition,
                R21Call.trajectory == stream.trajectory,
                R21Call.status == "failed_terminal",
            )
        )
        if failed:
            stream.status, stream.worker_id = "blocked", None
            group.status = "blocked"
            project.status = "attention_required"
            db.commit()
            return False
        _complete_stream(db, project, stream)
        return True
    input_json, messages, record, prior = _input_and_messages(db, project, spec)
    logical = _existing(db, project.id, spec)
    if logical is None:
        logical = R21Call(
            project_id=project.id,
            kind=spec.kind,
            status="pending",
            condition=spec.condition,
            question_id=spec.question_id,
            answer_id=spec.answer_id,
            group_id=record.group_id,
            trajectory=spec.trajectory,
            history_count=spec.history_count,
            repeat=spec.repeat,
            input_json=input_json,
            teacher_score=record.teacher_score,
            max_score=record.max_score,
        )
        db.add(logical)
        db.commit()
    else:
        logical.status, logical.failure_reason, logical.input_json = (
            "pending",
            None,
            input_json,
        )
        db.commit()
    try:
        runner = runner_factory()
        runner.assert_matches(project.runner_runtime_json)
    except RunnerDriftError as exc:
        project.status = "paused"
        stream.status, stream.worker_id = "pending", None
        db.add(
            R21Exposure(
                project_id=project.id,
                question_id=spec.question_id,
                action="runner_drift",
                detail_json={"reason": str(exc)},
            )
        )
        db.commit()
        return False
    except RunnerUnavailableError:
        stream.status, stream.worker_id = "pending", None
        db.commit()
        return False
    succeeded = False
    previous_attempts = int(
        db.scalar(
            select(func.max(R21CallAttempt.attempt_number)).where(
                R21CallAttempt.call_id == logical.id
            )
        )
        or 0
    )
    for attempt_number in (previous_attempts + 1, previous_attempts + 2):
        try:
            if project.protocol_id == R22_PROTOCOL_ID:
                recovered = run_with_recovery(
                    runner,
                    messages=messages,
                    kind=(
                        "test_score"
                        if spec.kind in {"test_score", "train_score"}
                        else "memory_update"
                    ),
                    condition=spec.condition,
                    runtime=project.runner_runtime_json,
                    answer=record.student_answer,
                    question=record.question_text,
                    reference_answer=record.reference_answer,
                )
                result = runner.last_result if hasattr(runner, "last_result") else None
                value = recovered.value
                latency_ms = recovered.primary_latency_ms + (
                    recovered.compression.latency_ms if recovered.compression else 0
                )
                thread_id, exit_code = None, 0
                for audit in audit_payload(
                    project_id=project.id,
                    logical_call_key=f"{project.id}:{logical.id}",
                    kind=(
                        "test_score"
                        if spec.kind in {"test_score", "train_score"}
                        else "memory_update"
                    ),
                    condition=spec.condition,
                    outcome=recovered,
                    runtime=project.runner_runtime_json,
                ):
                    db.add(R22CompressionAudit(**audit))
            else:
                result = runner.run(
                    messages=messages,
                    schema=output_schema(spec.kind, spec.condition),
                    runtime=project.runner_runtime_json,
                )
                value = result.value
                latency_ms = result.latency_ms
                thread_id, exit_code = result.thread_id, result.exit_code
            if (
                spec.kind in {"test_score", "train_score"}
                and float(value["score"]) > record.max_score
            ):
                raise ValueError("score exceeds question maximum")
            if spec.kind == "memory_update":
                items, counts = parse_memory_update(
                    spec.condition,
                    value,
                    record.student_answer,
                    record.question_text,
                    record.reference_answer,
                )
            db.add(
                R21CallAttempt(
                    call_id=logical.id,
                    attempt_number=attempt_number,
                    requested_model=project.runner_runtime_json["model"],
                    reasoning_effort=project.runner_runtime_json["reasoning_effort"],
                    thread_id=thread_id,
                    exit_code=exit_code,
                    latency_ms=latency_ms,
                    status="succeeded",
                    output_sha256=hashlib.sha256(
                        json.dumps(value, sort_keys=True).encode()
                    ).hexdigest(),
                    stderr_excerpt=(
                        result.stderr_excerpt if result is not None else ""
                    ),
                    error_message=None,
                    started_at=utc_now_naive(),
                )
            )
            logical.output_json, logical.model_score, logical.status = (
                value,
                (
                    float(value["score"])
                    if spec.kind in {"test_score", "train_score"}
                    else None
                ),
                "succeeded",
            )
            if spec.kind == "memory_update":
                snapshot = _snapshot(db, project, spec, spec.history_count)
                if snapshot is None:
                    snapshot = R21Snapshot(
                        project_id=project.id,
                        question_id=spec.question_id,
                        condition=spec.condition,
                        trajectory=spec.trajectory,
                        history_count=spec.history_count,
                        items_json=items,
                        source_record_ids_json=(
                            list(prior.source_record_ids_json) if prior else []
                        )
                        + [record.answer_id],
                        visible_token_count=counts["visible_token_count"],
                        stored_token_count=counts["stored_token_count"],
                    )
                    db.add(snapshot)
            db.commit()
            succeeded = True
            break
        except RunnerInterruptedError as exc:
            db.add(
                R21CallAttempt(
                    call_id=logical.id,
                    attempt_number=attempt_number,
                    requested_model=project.runner_runtime_json["model"],
                    reasoning_effort=project.runner_runtime_json["reasoning_effort"],
                    thread_id=None,
                    exit_code=exc.exit_code,
                    latency_ms=0,
                    status="interrupted",
                    output_sha256=None,
                    stderr_excerpt=exc.stderr,
                    error_message=str(exc)[:4000],
                    started_at=utc_now_naive(),
                )
            )
            logical.status = "retry_pending"
            logical.failure_reason = str(exc)[:4000]
            stream.status, stream.worker_id = "pending", None
            db.commit()
            return False
        except TerminalRecoveryError as exc:
            db.add(
                R22CompressionAudit(
                    **terminal_audit_payload(
                        project_id=project.id,
                        logical_call_key=f"{project.id}:{logical.id}",
                        kind=(
                            "test_score"
                            if spec.kind in {"test_score", "train_score"}
                            else "memory_update"
                        ),
                        condition=spec.condition,
                        outcome="failed_terminal",
                        runtime=project.runner_runtime_json,
                        error_message=str(exc),
                        original=exc.candidate,
                    )
                )
            )
            logical.retry_count, logical.failure_reason = (
                attempt_number,
                str(exc)[:4000],
            )
            db.commit()
        except (RunnerExecutionError, ValueError) as exc:
            db.add(
                R21CallAttempt(
                    call_id=logical.id,
                    attempt_number=attempt_number,
                    requested_model=project.runner_runtime_json["model"],
                    reasoning_effort=project.runner_runtime_json["reasoning_effort"],
                    thread_id=None,
                    exit_code=getattr(exc, "exit_code", None),
                    latency_ms=0,
                    status="failed",
                    output_sha256=None,
                    stderr_excerpt=getattr(exc, "stderr", ""),
                    error_message=str(exc)[:4000],
                    started_at=utc_now_naive(),
                )
            )
            logical.retry_count, logical.failure_reason = (
                attempt_number,
                str(exc)[:4000],
            )
            db.commit()
    if not succeeded:
        logical.status, stream.status, stream.worker_id = (
            "failed_terminal",
            "blocked",
            None,
        )
        group.status = "blocked"
        project.status = "attention_required"
        db.add(
            R21Exposure(
                project_id=project.id,
                question_id=logical.question_id,
                action="terminal_failure",
                detail_json={"call_id": logical.id},
            )
        )
        db.commit()
        return False
    stream.status, stream.worker_id = "pending", None
    db.commit()
    return True
