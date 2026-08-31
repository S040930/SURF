"""Persistent r20 execution grid with paired evidence and auditable attempts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.gateway import (
    ConfigurationGatewayError,
    canonical_request_hash,
    complete_request,
)
from app.experiment.r20.analysis import ANALYSIS_VERSION, compute_analysis
from app.experiment.r20.memory import parse_memory_update
from app.experiment.r20.prompts import render_memory
from app.experiment.r20.protocol import (
    FINAL_REPEATS,
    MAX_VISIBLE_TOKENS,
    PROBE_TEST_COUNT,
    PromptTemplates,
    schedule_from_manifest,
)
from app.experiment.r20.runner import make_chat_request, score_messages, update_messages
from app.experiment.types import ModelConfig
from app.models.r20 import (
    R20Call,
    R20CallAttempt,
    R20Project,
    R20Record,
    R20Report,
    R20RunGroup,
    R20Snapshot,
    R20Stream,
)

LEASE_GRACE_SECONDS = 30
AUTO_RETRY_SECONDS = int(
    os.getenv("R20_AUTO_RETRY_SECONDS") or settings.R20_AUTO_RETRY_SECONDS
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CallSpec:
    kind: str
    model_id: str
    question_id: str
    condition: str
    trajectory: int
    answer_id: str
    history_count: int
    repeat: int


def expected_question_calls(
    memory_count: int, test_count: int, probe_checkpoint_count: int
) -> int:
    # Two memory conditions x two models x three trajectories, sparse probes
    # across all three conditions, and two final repeats.
    return (
        12 * memory_count
        + 18 * PROBE_TEST_COUNT * probe_checkpoint_count
        + 36 * test_count
    )


def _records(
    db: Session, project_id: str, question: str, usage: str, trajectory: int = 0
):
    query = select(R20Record).where(
        R20Record.project_id == project_id,
        R20Record.question_id == question,
        R20Record.usage == usage,
    )
    if usage == "memory":
        query = query.where(R20Record.trajectory == trajectory)
    return list(db.scalars(query.order_by(R20Record.position)))


def call_sequence(
    db: Session, project: R20Project, stream: R20Stream
) -> list[CallSpec]:
    schedule = schedule_from_manifest(project.manifest_json, project.kind)
    memory = _records(db, project.id, stream.question_id, "memory", stream.trajectory)
    tests = _records(db, project.id, stream.question_id, "test")
    probes = [row for row in tests if row.probe]
    specs = []
    if stream.condition != "nm":
        for position, row in enumerate(memory, start=1):
            specs.append(
                CallSpec(
                    "memory_update",
                    stream.model_id,
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
                        stream.model_id,
                        stream.question_id,
                        stream.condition,
                        stream.trajectory,
                        test.answer_id,
                        position,
                        1,
                    )
                    for test in probes
                )
        specs.extend(
            CallSpec(
                "test_score",
                stream.model_id,
                stream.question_id,
                stream.condition,
                stream.trajectory,
                test.answer_id,
                schedule.final_history,
                repeat,
            )
            for test in tests
            for repeat in FINAL_REPEATS
        )
    else:
        for history in schedule.probe_checkpoints:
            specs.extend(
                CallSpec(
                    "test_score",
                    stream.model_id,
                    stream.question_id,
                    "nm",
                    stream.trajectory,
                    test.answer_id,
                    history,
                    1,
                )
                for test in probes
            )
        specs.extend(
            CallSpec(
                "test_score",
                stream.model_id,
                stream.question_id,
                "nm",
                stream.trajectory,
                test.answer_id,
                schedule.final_history,
                repeat,
            )
            for test in tests
            for repeat in FINAL_REPEATS
        )
    return specs


def _existing(db: Session, project_id: str, spec: CallSpec):
    return db.scalar(
        select(R20Call).where(
            R20Call.project_id == project_id,
            R20Call.model_id == spec.model_id,
            R20Call.question_id == spec.question_id,
            R20Call.condition == spec.condition,
            R20Call.trajectory == spec.trajectory,
            R20Call.kind == spec.kind,
            R20Call.answer_id == spec.answer_id,
            R20Call.history_count == spec.history_count,
            R20Call.repeat == spec.repeat,
        )
    )


def next_call(db: Session, project: R20Project, stream: R20Stream) -> CallSpec | None:
    for spec in call_sequence(db, project, stream):
        row = _existing(db, project.id, spec)
        if row is not None and row.status == "failed_terminal":
            # Strict serial execution: a terminal failure blocks every call
            # after it until the failure is manually retried.
            return None
        if row is None:
            if spec.kind == "test_score" and spec.condition != "nm":
                snapshot = db.scalar(
                    select(R20Snapshot).where(
                        R20Snapshot.project_id == project.id,
                        R20Snapshot.model_id == spec.model_id,
                        R20Snapshot.question_id == spec.question_id,
                        R20Snapshot.condition == spec.condition,
                        R20Snapshot.trajectory == spec.trajectory,
                        R20Snapshot.history_count == spec.history_count,
                    )
                )
                if snapshot is None:
                    return None
            return spec
    return None


def has_unrecorded(db: Session, project: R20Project, stream: R20Stream) -> bool:
    return any(
        _existing(db, project.id, spec) is None
        for spec in call_sequence(db, project, stream)
    )


def has_retry_pending(db: Session, project: R20Project, stream: R20Stream) -> bool:
    return (
        db.scalar(
            select(R20Call.id)
            .where(
                R20Call.project_id == project.id,
                R20Call.model_id == stream.model_id,
                R20Call.question_id == stream.question_id,
                R20Call.condition == stream.condition,
                R20Call.trajectory == stream.trajectory,
                R20Call.status == "retry_pending",
            )
            .limit(1)
        )
        is not None
    )


def next_retry(db: Session, project: R20Project, stream: R20Stream) -> CallSpec | None:
    now = utc_now_naive()
    for spec in call_sequence(db, project, stream):
        row = _existing(db, project.id, spec)
        if row is None or row.status != "retry_pending":
            continue
        if row.next_retry_at is not None and row.next_retry_at > now:
            continue
        if spec.kind == "test_score" and spec.condition != "nm":
            snapshot = _snapshot(db, project.id, spec, spec.history_count)
            if snapshot is None:
                continue
        return spec
    return None


def _record(db: Session, project_id: str, spec: CallSpec) -> R20Record:
    usage = "memory" if spec.kind == "memory_update" else "test"
    query = select(R20Record).where(
        R20Record.project_id == project_id,
        R20Record.answer_id == spec.answer_id,
        R20Record.usage == usage,
    )
    if usage == "memory":
        query = query.where(R20Record.trajectory == spec.trajectory)
    row = db.scalar(query)
    if row is None:
        raise RuntimeError(f"r20 record missing: {spec.answer_id}")
    return row


def _snapshot(db: Session, project_id: str, spec: CallSpec, history: int):
    return db.scalar(
        select(R20Snapshot).where(
            R20Snapshot.project_id == project_id,
            R20Snapshot.model_id == spec.model_id,
            R20Snapshot.question_id == spec.question_id,
            R20Snapshot.condition == spec.condition,
            R20Snapshot.trajectory == spec.trajectory,
            R20Snapshot.history_count == history,
        )
    )


def _model(project: R20Project, model_id: str) -> ModelConfig:
    for config in project.model_configs_json:
        if config["requested_model"] == model_id:
            model = ModelConfig.model_validate(config)
            override = os.getenv("R20_TIMEOUT_SECONDS")
            if not override:
                override = settings.R20_TIMEOUT_SECONDS
            if override is not None and str(override).strip():
                model = model.model_copy(update={"timeout_seconds": float(override)})
            return model
    raise RuntimeError(f"frozen r20 model missing: {model_id}")


def _save_attempt(db: Session, call_id: int, attempt, offset: int = 0) -> None:
    db.add(
        R20CallAttempt(
            call_id=call_id,
            attempt_number=attempt.attempt_number + offset,
            request_sha256=attempt.request_hash,
            status=attempt.status,
            requested_model=attempt.requested_model,
            returned_model=attempt.returned_model,
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            total_tokens=attempt.total_tokens,
            latency_ms=attempt.latency_ms,
            error_type=attempt.error_type,
            error_message=attempt.error_message,
            provider_request_id=attempt.provider_request_id,
            system_fingerprint=attempt.system_fingerprint,
            started_at=attempt.started_at,
        )
    )
    db.commit()


def execute_call(
    db: Session,
    project: R20Project,
    stream: R20Stream,
    spec: CallSpec,
    transport=None,
    retry_row: R20Call | None = None,
) -> None:
    model = _model(project, spec.model_id)
    templates = PromptTemplates.model_validate(project.prompt_templates_json)
    row = _record(db, project.id, spec)
    if spec.kind == "test_score":
        snapshot = (
            None
            if spec.condition == "nm"
            else _snapshot(db, project.id, spec, spec.history_count)
        )
        items = list(snapshot.items_json) if snapshot else []
        memory = (
            render_memory(spec.condition, items)
            if items
            else "No learned memory is available for this question."
        )
        messages = score_messages(
            templates.scoring,
            row.question_text,
            row.reference_answer,
            row.student_answer,
            row.max_score,
            memory,
        )
        # Explicit allowlist: labels, feedback, group ID and support never enter scoring.
        input_json = {
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "max_score": row.max_score,
            "memory": memory,
        }

        def validator(parsed):
            if parsed.score > row.max_score:
                raise ValueError("score exceeds question maximum")

    else:
        prior = _snapshot(db, project.id, spec, spec.history_count - 1)
        items = list(prior.items_json) if prior else []
        instruction = (
            templates.crm_update if spec.condition == "crm" else templates.arm_update
        )
        messages = update_messages(
            instruction,
            row.question_text,
            row.reference_answer,
            row.student_answer,
            row.max_score,
            row.teacher_score,
            row.teacher_feedback,
            items,
        )
        input_json = {
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "max_score": row.max_score,
            "teacher_score": row.teacher_score,
            "teacher_feedback": row.teacher_feedback,
            "existing_memory": items,
        }

        def validator(parsed):
            parse_memory_update(
                spec.condition,
                parsed.model_dump(mode="json"),
                row.student_answer,
                row.question_text,
                row.reference_answer,
            )

    request = make_chat_request(model, messages, spec.kind, spec.condition, validator)
    if retry_row is not None:
        logical = retry_row
        logical.status = "pending"
        logical.output_json = None
        logical.model_score = None
        logical.failure_reason = None
        logical.input_json = input_json
        logical.teacher_score = row.teacher_score
        logical.max_score = row.max_score
        logical.request_sha256 = canonical_request_hash(request.payload())
        attempt_offset = int(
            db.scalar(
                select(func.max(R20CallAttempt.attempt_number)).where(
                    R20CallAttempt.call_id == logical.id
                )
            )
            or 0
        )
    else:
        attempt_offset = 0
        logical = R20Call(
            project_id=project.id,
            model_id=spec.model_id,
            kind=spec.kind,
            status="pending",
            condition=spec.condition,
            question_id=spec.question_id,
            answer_id=spec.answer_id,
            group_id=row.group_id,
            trajectory=spec.trajectory,
            history_count=spec.history_count,
            repeat=spec.repeat,
            input_json=input_json,
            teacher_score=row.teacher_score,
            max_score=row.max_score,
            request_sha256=canonical_request_hash(request.payload()),
        )
    db.add(logical)
    db.flush()
    logical_id = logical.id
    owner = stream.worker_id

    def before_attempt(_number: int, _hash: str) -> None:
        if owner is None:
            return
        locked = db.scalar(
            select(R20Stream).where(R20Stream.id == stream.id).with_for_update()
        )
        if locked is None or locked.status != "leased" or locked.worker_id != owner:
            raise RuntimeError("r20 stream lease ownership was lost")
        locked.lease_until = utc_now_naive() + timedelta(
            seconds=float(model.timeout_seconds) + LEASE_GRACE_SECONDS
        )
        db.commit()

    try:
        result = asyncio.run(
            complete_request(
                request,
                transport=transport,
                on_attempt=lambda attempt: _save_attempt(
                    db, logical.id, attempt, attempt_offset
                ),
                before_attempt=before_attempt,
            )
        )
        logical.output_json = result.parsed.model_dump(mode="json")
        logical.model_score = (
            float(result.parsed.score) if spec.kind == "test_score" else None
        )
        logical.request_sha256 = result.request_hash
        logical.status = "succeeded"
        if spec.kind == "memory_update":
            parsed, counts = parse_memory_update(
                spec.condition,
                logical.output_json,
                row.student_answer,
                row.question_text,
                row.reference_answer,
            )
            existing = db.scalar(
                select(R20Snapshot).where(
                    R20Snapshot.project_id == project.id,
                    R20Snapshot.model_id == spec.model_id,
                    R20Snapshot.question_id == spec.question_id,
                    R20Snapshot.condition == spec.condition,
                    R20Snapshot.trajectory == spec.trajectory,
                    R20Snapshot.history_count == spec.history_count,
                )
            )
            source_ids = (list(prior.source_record_ids_json) if prior else []) + [
                row.answer_id
            ]
            if existing is None:
                existing = R20Snapshot(
                    project_id=project.id,
                    model_id=spec.model_id,
                    question_id=spec.question_id,
                    condition=spec.condition,
                    trajectory=spec.trajectory,
                    history_count=spec.history_count,
                )
                db.add(existing)
            existing.items_json = parsed
            existing.source_record_ids_json = source_ids
            existing.visible_token_count = counts["visible_token_count"]
            existing.stored_token_count = counts["stored_token_count"]
    except Exception as exc:
        db.rollback()
        if db.scalar(select(R20Call.id).where(R20Call.id == logical_id)) is None:
            logger.warning(
                "r20 call %s no longer exists (deleted while in flight); skipping",
                logical_id,
            )
            return
        logical = db.get(R20Call, logical_id, populate_existing=True)
        logical.failure_reason = str(exc)
        if isinstance(exc, ConfigurationGatewayError):
            logical.status = "failed_terminal"
            logical.retry_count = 0
            logical.next_retry_at = None
        else:
            # Transient or ambiguous (e.g. provider timeout) failure: retry
            # once after the configured interval. Two total attempts per call
            # (initial + one scheduled retry) bound repeated re-inference
            # costs; a second failure becomes terminal.
            logical.retry_count += 1
            if logical.retry_count >= 2:
                logical.status = "failed_terminal"
                logical.next_retry_at = None
            else:
                logical.status = "retry_pending"
                logical.next_retry_at = utc_now_naive() + timedelta(
                    seconds=AUTO_RETRY_SECONDS
                )
    if (
        db.scalar(
            select(R20Call.id).where(R20Call.id == logical_id),
            execution_options={"autoflush": False},
        )
        is None
    ):
        logger.warning(
            "r20 call %s no longer exists (deleted while in flight); skipping commit",
            logical_id,
        )
        db.rollback()
        return
    db.commit()


def _resource_summary(db: Session, project: R20Project) -> dict:
    attempts = list(
        db.scalars(
            select(R20CallAttempt).join(R20Call).where(R20Call.project_id == project.id)
        )
    )
    return {
        "attempts": len(attempts),
        "input_tokens": sum(row.input_tokens or 0 for row in attempts),
        "output_tokens": sum(row.output_tokens or 0 for row in attempts),
        "latency_ms": sum(row.latency_ms or 0 for row in attempts),
        "latencies_ms": [
            row.latency_ms for row in attempts if row.latency_ms is not None
        ],
        "estimated_cost_fen": sum(
            (
                (row.input_tokens or 0)
                * _model(project, row.requested_model).input_cost_fen_per_million
                + (row.output_tokens or 0)
                * _model(project, row.requested_model).output_cost_fen_per_million
            )
            / 1_000_000
            for row in attempts
        ),
        "failed_calls": int(
            db.scalar(
                select(func.count())
                .select_from(R20Call)
                .where(R20Call.project_id == project.id, R20Call.status != "succeeded")
            )
            or 0
        ),
    }


def _manipulation_summary(db: Session, project: R20Project) -> dict:
    snapshots = list(
        db.scalars(select(R20Snapshot).where(R20Snapshot.project_id == project.id))
    )
    violations = [
        row.id
        for row in snapshots
        if len(row.items_json) > 6 or row.visible_token_count > MAX_VISIBLE_TOKENS
    ]
    return {
        "snapshot_count": len(snapshots),
        "max_items": max((len(row.items_json) for row in snapshots), default=0),
        "max_visible_tokens": max(
            (row.visible_token_count for row in snapshots), default=0
        ),
        "constraint_violation_count": len(violations),
    }


def lock_report(db: Session, project: R20Project) -> R20Report:
    existing = db.scalar(select(R20Report).where(R20Report.project_id == project.id))
    if existing:
        return existing
    calls = list(
        db.scalars(
            select(R20Call).where(R20Call.project_id == project.id).order_by(R20Call.id)
        )
    )
    rows = [
        {
            "model_id": row.model_id,
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
    failures = [
        {"call_id": row.id, "reason": row.failure_reason}
        for row in calls
        if row.status != "succeeded"
    ]
    resources = _resource_summary(db, project)
    manipulation = _manipulation_summary(db, project)
    if project.kind == "pilot_run":
        result = {
            "core": None,
            "supplement": {
                "resources": resources,
                "failures": failures,
                "manipulation_checks": manipulation,
            },
            "analysis_status": "pilot_technical_only",
        }
    elif failures:
        result = {
            "core": None,
            "supplement": {
                "resources": resources,
                "failures": failures,
                "manipulation_checks": manipulation,
            },
            "analysis_status": "incomplete_due_to_terminal_failures",
        }
    else:
        schedule = schedule_from_manifest(project.manifest_json, project.kind)
        result = compute_analysis(
            rows, resources, failures, final_history=schedule.final_history
        )
        result["supplement"]["manipulation_checks"] = manipulation
    report_inputs = {
        "calls": rows,
        "failures": failures,
        "resources": resources,
        "manifest_sha256": project.manifest_sha256,
        "analysis_code_sha256": project.analysis_code_sha256,
    }
    input_hash = hashlib.sha256(
        json.dumps(report_inputs, sort_keys=True).encode()
    ).hexdigest()
    report_hash = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()
    ).hexdigest()
    report = R20Report(
        project_id=project.id,
        report_json=result,
        input_sha256=input_hash,
        report_sha256=report_hash,
        analysis_version=ANALYSIS_VERSION,
        analysis_code_sha256=project.analysis_code_sha256,
    )
    project.report_sha256 = report_hash
    db.add(report)
    db.commit()
    return report


def complete_stream(db: Session, project: R20Project, stream: R20Stream) -> None:
    stream.status = "completed"
    stream.worker_id = None
    stream.lease_until = None
    db.commit()
    group = db.scalar(
        select(R20RunGroup).where(
            R20RunGroup.project_id == project.id,
            R20RunGroup.question_id == stream.question_id,
            R20RunGroup.condition == stream.condition,
        )
    )
    if group is not None and group.status != "completed":
        group_unfinished = db.scalar(
            select(func.count())
            .select_from(R20Stream)
            .where(
                R20Stream.project_id == project.id,
                R20Stream.question_id == stream.question_id,
                R20Stream.condition == stream.condition,
                R20Stream.status != "completed",
            )
        )
        if not group_unfinished:
            group.status = "completed"
            group.completed_at = utc_now_naive()
            db.commit()
    unfinished = db.scalar(
        select(func.count())
        .select_from(R20RunGroup)
        .where(
            R20RunGroup.project_id == project.id,
            R20RunGroup.status != "completed",
        )
    )
    if unfinished:
        return
    db.refresh(project)
    if project.status == "terminated":
        return
    failed = db.scalar(
        select(func.count())
        .select_from(R20Call)
        .where(R20Call.project_id == project.id, R20Call.status != "succeeded")
    )
    project.status = "completed_with_failures" if failed else "completed"
    project.completed_at = utc_now_naive()
    db.commit()
    lock_report(db, project)
