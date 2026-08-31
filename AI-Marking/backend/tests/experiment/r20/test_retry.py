"""Manual per-call retry for r20 terminal failures."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.r20_platform import retry_call
from app.core.time import utc_now_naive
from app.experiment.gateway import TransportResponse
from app.experiment.r20.executor import (
    execute_call,
    has_retry_pending,
    next_retry,
)
from app.experiment.r20.protocol import PromptTemplates
from app.models.r20 import (
    R20Call,
    R20CallAttempt,
    R20Exposure,
    R20Project,
    R20Record,
    R20Report,
    R20RunGroup,
    R20Snapshot,
    R20Stream,
)

TEMPLATES = PromptTemplates(
    scoring="Grade using the question and reference answer only.",
    crm_update="Return a compact replacement rule snapshot.",
    arm_update="Return a compact replacement rubric snapshot.",
)


def _project(db, status="running"):
    project = R20Project(
        id=str(uuid4()),
        name=f"retry-{uuid4().hex[:6]}",
        kind="pilot_run",
        status=status,
        model_config_ids_json=["a", "b"],
        model_configs_json=[
            {
                "provider": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "requested_model": "test-model",
                "expected_returned_model": "test-model",
                "api_key_env": "",
                "temperature": 0,
                "timeout_seconds": 5,
                "input_cost_fen_per_million": 0,
                "output_cost_fen_per_million": 0,
            }
        ],
        model_configs_sha256="m" * 64,
        prompt_version_id="p",
        prompt_version_name="p",
        prompt_templates_json=TEMPLATES.model_dump(mode="json"),
        prompt_version_sha256="p" * 64,
        manifest_json={
            "expected_calls": 510,
            "schedule": {
                "memory_count": 20,
                "checkpoints": [10, 20],
                "probe_checkpoints": [10],
                "test_count": 5,
                "final_history": 20,
            },
        },
        manifest_sha256="x" * 64,
        data_sha256="d" * 64,
        analysis_code_sha256="a" * 64,
    )
    db.add(project)
    return project


def _record(db, project, answer_id="answer-1", usage="test", position=1):
    row = R20Record(
        project_id=project.id,
        answer_id=answer_id,
        group_id="group-1",
        question_id="question-1",
        question_text="Question?",
        reference_answer="Reference.",
        student_answer="Student answer.",
        teacher_score=1.0,
        teacher_feedback="Good.",
        max_score=3.0,
        source_split="test_unseen_answers",
        usage=usage,
        trajectory=0 if usage == "test" else 1,
        position=position,
        probe=False,
    )
    db.add(row)
    return row


def _stream(db, project):
    stream = R20Stream(
        project_id=project.id,
        model_id="test-model",
        question_id="question-1",
        condition="nm",
        trajectory=1,
        order_rank=0,
        status="pending",
    )
    db.add(stream)
    return stream


def _failed_call(db, project, stream, kind="test_score", history=20):
    call = R20Call(
        project_id=project.id,
        model_id=stream.model_id,
        kind=kind,
        status="failed_terminal",
        condition=stream.condition,
        question_id=stream.question_id,
        answer_id="answer-1" if kind == "test_score" else "answer-2",
        group_id="group-1",
        trajectory=stream.trajectory,
        history_count=history,
        repeat=0 if kind == "memory_update" else 1,
        input_json={},
        failure_reason="boom",
    )
    db.add(call)
    db.flush()
    db.add(
        R20CallAttempt(
            call_id=call.id,
            attempt_number=1,
            request_sha256="a" * 64,
            status="success",
            requested_model="test-model",
            started_at=call.created_at,
        )
    )
    return call


class ScoringTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.sent = 0

    async def send(self, payload):
        self.sent += 1
        if self.sent > len(self._responses):
            raise RuntimeError("unexpected transport use")
        raw = self._responses[self.sent - 1]
        return TransportResponse(raw, "test-model", 1, 1, 2, f"request-{self.sent}")


def _project_with_stream_and_call(db, status="running", stream_status="pending"):
    project = _project(db, status)
    stream = _stream(db, project)
    _record(db, project, answer_id="answer-1", usage="test", position=1)
    call = _failed_call(db, project, stream)
    db.commit()
    return project, stream, call


def test_retry_call_moves_failed_terminal_to_retry_pending(db_session):
    project, stream, call = _project_with_stream_and_call(db_session)
    db_session.add(
        R20Exposure(project_id=project.id, question_id=call.question_id, action="x")
    )
    db_session.commit()

    retry_call(project.id, call.id, db_session)

    row = db_session.get(R20Call, call.id)
    assert row.status == "retry_pending"
    assert row.failure_reason is None
    stream = db_session.scalar(select(R20Stream).where(R20Stream.id == stream.id))
    assert stream.status == "pending"
    assert stream.worker_id is None
    assert db_session.query(R20Exposure).filter_by(action="manual_retry").count() == 1


def test_retry_call_while_stream_leased_enqueues_without_touching_stream(db_session):
    """stream 被 worker 占用时手动重试：纯入队，绝不触碰 stream 行。"""
    project, stream, call = _project_with_stream_and_call(db_session)
    stream.status = "leased"
    stream.worker_id = "worker-9"
    stream.lease_until = utc_now_naive() + timedelta(seconds=60)
    db_session.commit()

    retry_call(project.id, call.id, db_session)

    row = db_session.get(R20Call, call.id)
    assert row.status == "retry_pending"
    assert row.failure_reason is None
    assert row.retry_count == 0
    assert row.next_retry_at is None
    stream_row = db_session.scalar(select(R20Stream).where(R20Stream.id == stream.id))
    assert stream_row.status == "leased"
    assert stream_row.worker_id == "worker-9"
    assert stream_row.lease_until is not None
    assert db_session.query(R20Exposure).filter_by(action="manual_retry").count() == 1


def test_retry_call_already_retry_pending_is_idempotent(db_session):
    """调用已在自动重试队列中：再次手动重试为幂等成功，不重复入队。"""
    project, stream, call = _project_with_stream_and_call(db_session)
    call.status = "retry_pending"
    call.retry_count = 1
    call.failure_reason = "still pending"
    db_session.commit()

    retry_call(project.id, call.id, db_session)

    row = db_session.get(R20Call, call.id)
    assert row.status == "retry_pending"
    assert row.retry_count == 1
    assert row.failure_reason == "still pending"
    assert db_session.query(R20Exposure).filter_by(action="manual_retry").count() == 0


def test_retry_call_rejects_terminated_project(db_session):
    project, stream, call = _project_with_stream_and_call(
        db_session, status="terminated"
    )
    with pytest.raises(HTTPException) as caught:
        retry_call(project.id, call.id, db_session)
    assert caught.value.status_code == 409


def test_retry_call_allowed_while_paused_queues_until_resume(db_session):
    project, stream, call = _project_with_stream_and_call(db_session, status="paused")
    db_session.add(
        R20Exposure(project_id=project.id, question_id=call.question_id, action="x")
    )
    db_session.commit()

    retry_call(project.id, call.id, db_session)

    row = db_session.get(R20Call, call.id)
    assert row.status == "retry_pending"
    assert row.failure_reason is None
    assert row.next_retry_at is None
    assert (
        db_session.scalar(select(R20Stream).where(R20Stream.id == stream.id)).status
        == "pending"
    )
    assert db_session.query(R20Exposure).filter_by(action="manual_retry").count() == 1


def test_retry_call_from_completed_with_failures_deletes_locked_report(db_session):
    project, stream, call = _project_with_stream_and_call(
        db_session, status="completed_with_failures", stream_status="completed"
    )
    stream.status = "completed"
    group = R20RunGroup(
        project_id=project.id,
        question_id=call.question_id,
        condition=call.condition,
        status="completed",
        order_rank=0,
        expected_calls=10,
        completed_at=utc_now_naive(),
    )
    db_session.add(group)
    db_session.add(
        R20Report(
            project_id=project.id,
            report_json={"core": None},
            input_sha256="i" * 64,
            report_sha256="r" * 64,
            analysis_version="v1",
            analysis_code_sha256="a" * 64,
        )
    )
    db_session.commit()

    retry_call(project.id, call.id, db_session)

    project = db_session.get(R20Project, project.id)
    assert project.status == "running"
    assert project.completed_at is None
    assert project.report_sha256 is None
    assert db_session.query(R20Report).count() == 0
    group = db_session.scalar(
        select(R20RunGroup).where(R20RunGroup.project_id == project.id)
    )
    assert group is not None
    assert group.status == "queued"
    assert group.completed_at is None


def test_retry_attempt_numbers_continue_past_existing(db_session):
    project, stream, call = _project_with_stream_and_call(db_session)
    retry_call(project.id, call.id, db_session)
    transport = ScoringTransport(['{"score":1,"feedback":"Ok."}'])
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "succeeded"
    attempts = list(
        db_session.scalars(
            select(R20CallAttempt)
            .where(R20CallAttempt.call_id == call.id)
            .order_by(R20CallAttempt.attempt_number)
        )
    )
    assert [row.attempt_number for row in attempts] == [1, 2]
    assert attempts[1].request_sha256 != attempts[0].request_sha256


def test_transient_memory_update_failure_retries_and_no_blocked_dependency(db_session):
    project = _project(db_session)
    stream = _stream(db_session, project)
    stream.condition = "crm"
    stream.status = "completed"
    db_session.flush()
    _record(db_session, project, answer_id="answer-2", usage="memory", position=1)
    failed = _failed_call(db_session, project, stream, kind="memory_update", history=1)
    # Simulate a transient failure: the call is awaiting its next retry, not a
    # terminal (config) failure.
    failed.status = "retry_pending"
    failed.retry_count = 0
    failed.next_retry_at = None
    db_session.commit()

    # Transient failure during replay keeps the call retry_pending (infinite),
    # never failed_terminal, and never creates a blocked_dependency.
    from app.experiment.gateway import RetriableGatewayError

    class FailingMemoryUpdateTransport:
        async def send(self, payload):
            raise RetriableGatewayError("boom")

    spec = next_retry(db_session, project, stream)
    assert spec is not None
    assert spec.kind == "memory_update"
    execute_call(
        db_session,
        project,
        stream,
        spec,
        transport=FailingMemoryUpdateTransport(),
        retry_row=failed,
    )
    db_session.refresh(failed)
    assert failed.status == "retry_pending"
    assert failed.retry_count == 1
    assert (
        db_session.scalar(select(R20Call).where(R20Call.status == "blocked_dependency"))
        is None
    )
    assert has_retry_pending(db_session, project, stream)

    # Once the memory update succeeds, the experiment continues normally.
    # Clear the backoff so the retry is immediately due again.
    failed.next_retry_at = None
    db_session.commit()
    transport = ScoringTransport(
        [
            '{"rules":[{"condition":"If a future answer includes reference",'
            '"effect":"supports","importance":"major",'
            '"guidance":"Reference is addressed.","support":"Teacher evidence supports this pattern."}]}'
        ]
    )
    spec = next_retry(db_session, project, stream)
    execute_call(
        db_session, project, stream, spec, transport=transport, retry_row=failed
    )
    db_session.refresh(failed)
    assert failed.status == "succeeded"
    assert not has_retry_pending(db_session, project, stream)
    snapshot = db_session.scalar(
        select(R20Snapshot).where(R20Snapshot.project_id == project.id)
    )
    assert snapshot is not None
    assert snapshot.history_count == 1


def test_next_retry_skips_test_score_without_snapshot(db_session):
    project = _project(db_session)
    stream = _stream(db_session, project)
    stream.condition = "crm"
    db_session.flush()
    for position in range(1, 11):
        _record(
            db_session,
            project,
            answer_id=f"answer-{position}",
            usage="memory",
            position=position,
        )
    probe = _record(db_session, project, answer_id="answer-3", usage="test", position=1)
    probe.probe = True
    call = _failed_call(db_session, project, stream, kind="test_score", history=10)
    call.answer_id = "answer-3"
    call.status = "retry_pending"
    call.failure_reason = None
    db_session.commit()
    assert next_retry(db_session, project, stream) is None

    db_session.add(
        R20Snapshot(
            project_id=project.id,
            model_id="test-model",
            question_id="question-1",
            condition="crm",
            trajectory=1,
            history_count=10,
            items_json=[],
            source_record_ids_json=[],
            visible_token_count=0,
            stored_token_count=0,
        )
    )
    db_session.commit()
    assert next_retry(db_session, project, stream) is not None
