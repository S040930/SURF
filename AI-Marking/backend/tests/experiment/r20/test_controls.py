"""Project controls: auto retry scheduling, pause/resume, and deletion."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from app.api import r20_platform as platform_module
from app.api.r20_platform import (
    delete_project,
    pause_project,
    resume_project,
    retry_call,
    run_groups,
    start_run_group,
)
from app.core.time import utc_now_naive
from app.experiment.gateway import (
    AmbiguousGatewayError,
    ConfigurationGatewayError,
    RetriableGatewayError,
)
from app.experiment.r20 import worker as worker_module
from app.experiment.r20.executor import (
    AUTO_RETRY_SECONDS,
    execute_call,
    next_retry,
)
from app.experiment.r20.protocol import PromptTemplates
from app.experiment.r20.worker import _lease, _lease_retry, _loop
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
        name=f"ctrl-{uuid4().hex[:6]}",
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


def _call(db, project, stream, status="failed_terminal", kind="test_score"):
    call = R20Call(
        project_id=project.id,
        model_id=stream.model_id,
        kind=kind,
        status=status,
        condition=stream.condition,
        question_id=stream.question_id,
        answer_id="answer-1" if kind == "test_score" else "answer-2",
        group_id="group-1",
        trajectory=stream.trajectory,
        history_count=20,
        repeat=0 if kind == "memory_update" else 1,
        input_json={},
        failure_reason="boom",
    )
    db.add(call)
    return call


class FailingTransport:
    def __init__(self, error, times=1):
        self.error = error
        self._left = times
        self.sent = 0

    async def send(self, payload):
        self.sent += 1
        if self._left > 0:
            self._left -= 1
            raise self.error
        from app.experiment.gateway import TransportResponse

        return TransportResponse(
            '{"score":1,"feedback":"Ok."}', "test-model", 1, 1, 2, "request-1"
        )


def _group(
    db,
    project,
    condition="nm",
    question="question-1",
    status="running",
    rank=0,
    expected=10,
):
    group = R20RunGroup(
        project_id=project.id,
        question_id=question,
        condition=condition,
        status=status,
        order_rank=rank,
        expected_calls=expected,
    )
    db.add(group)
    return group


def _ready(db, status="running", call_status="failed_terminal"):
    project = _project(db, status)
    _group(db, project)
    stream = _stream(db, project)
    _record(db, project, answer_id="answer-1", usage="test", position=1)
    call = _call(db, project, stream, status=call_status)
    db.commit()
    return project, stream, call


def test_auto_retry_schedules_pending_after_failure(db_session):
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = None
    call.retry_count = 0
    db_session.commit()
    transport = FailingTransport(RetriableGatewayError("boom"), times=10)
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "retry_pending"
    assert call.retry_count == 1
    assert call.next_retry_at is not None
    gap = (call.next_retry_at - utc_now_naive()).total_seconds()
    assert AUTO_RETRY_SECONDS - 5 <= gap <= AUTO_RETRY_SECONDS + 5


def test_auto_retry_waits_for_next_retry_at(db_session):
    project, stream, call = _ready(db_session)
    call.status = "retry_pending"
    call.retry_count = 1
    call.next_retry_at = utc_now_naive() + timedelta(seconds=3600)
    call.failure_reason = None
    db_session.commit()
    assert next_retry(db_session, project, stream) is None


def test_auto_retry_executes_after_deadline(db_session):
    project, stream, call = _ready(db_session)
    call.status = "retry_pending"
    call.retry_count = 1
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    call.failure_reason = None
    db_session.commit()
    transport = FailingTransport(RetriableGatewayError("boom"), times=10)
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "failed_terminal"
    assert call.retry_count == 2
    assert call.next_retry_at is None


def test_auto_retry_is_capped_never_infinite(db_session):
    project, stream, call = _ready(db_session)
    call.retry_count = 5  # far beyond the two-attempt budget
    call.status = "retry_pending"
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    call.failure_reason = None
    db_session.commit()
    transport = FailingTransport(RetriableGatewayError("boom"), times=10)
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "failed_terminal"
    assert call.retry_count == 6
    assert call.next_retry_at is None


def test_configuration_error_skips_auto_retry(db_session):
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = None
    call.retry_count = 0
    db_session.commit()
    transport = FailingTransport(ConfigurationGatewayError("bad config"), times=10)
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "failed_terminal"
    assert call.retry_count == 0
    assert call.next_retry_at is None


def test_ambiguous_error_auto_retries(db_session):
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = None
    call.retry_count = 0
    db_session.commit()
    transport = FailingTransport(AmbiguousGatewayError("Request timed out."), times=10)
    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(db_session, project, stream, spec, transport=transport, retry_row=call)
    db_session.refresh(call)
    assert call.status == "retry_pending"
    assert call.retry_count == 1
    assert call.next_retry_at is not None
    assert call.next_retry_at > utc_now_naive()


def test_pace_request_serializes_starts(monkeypatch):
    slept = []
    monkeypatch.setattr(worker_module, "MIN_REQUEST_INTERVAL", 0.02)
    monkeypatch.setattr(worker_module, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(worker_module.time, "sleep", slept.append)
    worker_module._pace_request()
    worker_module._pace_request()
    assert len(slept) == 1
    assert slept[0] > 0


def test_pace_request_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(worker_module, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(worker_module, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(
        worker_module.time, "sleep", lambda _: pytest.fail("must not sleep")
    )
    worker_module._pace_request()
    worker_module._pace_request()


def test_manual_retry_resets_auto_retry_counter(db_session):
    project, stream, call = _ready(db_session)
    call.retry_count = 2
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()
    retry_call(project.id, call.id, db_session)
    db_session.refresh(call)
    assert call.status == "retry_pending"
    assert call.retry_count == 0
    assert call.next_retry_at is None


def test_pause_and_resume_project(db_session):
    project, stream, call = _ready(db_session)
    paused = pause_project(project.id, db_session)
    assert paused["status"] == "paused"
    assert db_session.query(R20Exposure).filter_by(action="pause").count() == 1
    with pytest.raises(HTTPException) as caught:
        pause_project(project.id, db_session)
    assert caught.value.status_code == 409
    resumed = resume_project(project.id, db_session)
    assert resumed["status"] == "running"
    assert db_session.query(R20Exposure).filter_by(action="resume").count() == 1


def test_delete_terminates_running_then_removes_everything(db_session):
    project, stream, call = _ready(db_session, status="running")
    db_session.add(
        R20CallAttempt(
            call_id=call.id,
            attempt_number=1,
            request_sha256="a" * 64,
            status="success",
            requested_model="test-model",
            started_at=utc_now_naive(),
        )
    )
    db_session.add(
        R20Snapshot(
            project_id=project.id,
            model_id="test-model",
            question_id="question-1",
            condition="crm",
            trajectory=1,
            history_count=20,
            items_json=[],
            source_record_ids_json=[],
            visible_token_count=0,
            stored_token_count=0,
        )
    )
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
    db_session.add(
        R20Exposure(project_id=project.id, question_id="question-1", action="x")
    )
    db_session.commit()

    delete_project(project.id, db_session)

    for model in (
        R20Project,
        R20Record,
        R20Stream,
        R20RunGroup,
        R20Snapshot,
        R20Call,
        R20CallAttempt,
        R20Report,
        R20Exposure,
    ):
        assert db_session.query(model).count() == 0, model.__name__


def test_delete_waits_for_inflight_call_then_removes_everything(
    db_session, monkeypatch
):
    project, stream, call = _ready(db_session, status="running")
    stream.status = "leased"
    stream.worker_id = "worker-1"
    stream.lease_until = utc_now_naive() + timedelta(seconds=60)
    db_session.commit()

    released = False

    def fake_sleep(_seconds):
        nonlocal released
        if not released:
            released = True
            row = db_session.get(R20Stream, stream.id)
            row.status, row.worker_id, row.lease_until = "pending", None, None
            db_session.commit()

    monkeypatch.setattr(platform_module.time, "sleep", fake_sleep)
    delete_project(project.id, db_session)
    assert released
    for model in (
        R20Project,
        R20Record,
        R20Stream,
        R20RunGroup,
        R20Call,
        R20CallAttempt,
    ):
        assert db_session.query(model).count() == 0, model.__name__


def test_delete_waits_for_leased_stream_timeout_then_409(db_session, monkeypatch):
    project, stream, call = _ready(db_session, status="running")
    stream.status = "leased"
    stream.worker_id = "worker-1"
    stream.lease_until = utc_now_naive() + timedelta(seconds=60)
    db_session.commit()

    monkeypatch.setattr(platform_module, "DELETE_DRAIN_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(HTTPException) as caught:
        delete_project(project.id, db_session)
    assert caught.value.status_code == 409
    assert "进行中" in caught.value.detail
    db_session.rollback()
    assert db_session.get(R20Project, project.id).status == "terminated"


def test_execute_call_survives_call_row_deleted_mid_flight(db_session):
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = None
    call.retry_count = 0
    call.failure_reason = None
    db_session.commit()

    class DeleteMidFlightTransport:
        async def send(self, payload):
            db_session.execute(
                sa_delete(R20Call).where(R20Call.id == call.id),
                execution_options={"synchronize_session": False},
            )
            db_session.commit()
            from app.experiment.gateway import TransportResponse

            return TransportResponse(
                '{"score":1,"feedback":"Ok."}', "test-model", 1, 1, 2, "request-1"
            )

    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(
        db_session,
        project,
        stream,
        spec,
        transport=DeleteMidFlightTransport(),
        retry_row=call,
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(R20Call)
            .where(R20Call.project_id == project.id)
        )
        == 0
    )


def test_execute_call_skips_failure_path_when_call_deleted(db_session):
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = None
    call.retry_count = 0
    call.failure_reason = None
    db_session.commit()

    class DeleteThenFailTransport:
        async def send(self, payload):
            db_session.execute(
                sa_delete(R20Call).where(R20Call.id == call.id),
                execution_options={"synchronize_session": False},
            )
            db_session.commit()
            raise RetriableGatewayError("boom")

    spec = next_retry(db_session, project, stream)
    assert spec is not None
    execute_call(
        db_session,
        project,
        stream,
        spec,
        transport=DeleteThenFailTransport(),
        retry_row=call,
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(R20Call)
            .where(R20Call.project_id == project.id)
        )
        == 0
    )


def test_worker_survives_execute_call_exception(db_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    project, stream, call = _ready(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker_module, "SessionLocal", factory)
    monkeypatch.setattr(worker_module, "POLL_SECONDS", 0.05)

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "execute_call", boom)
    stop = threading.Event()

    def run():
        _loop(stop)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    db_session.rollback()
    stream = db_session.get(R20Stream, stream.id)
    assert stream.status in {"pending", "leased"}


def test_worker_lease_paused_while_retry_pending(db_session):
    """一个有待重试调用的项目暂停整个实验：_lease 不返回新工作流。"""
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()
    assert _lease(db_session, "worker-1") is None


def test_worker_lease_paused_while_terminal_failure(db_session):
    """failed_terminal 暂停整个实验组：_lease 不返回新工作流。"""
    project, stream, call = _ready(db_session, call_status="failed_terminal")
    db_session.commit()
    assert _lease(db_session, "worker-1") is None


def test_worker_lease_resumes_after_manual_retry(db_session):
    """手动重试入队后，_lease_retry 能串行拿到该流：手动/自动不竞争。"""
    project, stream, call = _ready(db_session, call_status="failed_terminal")
    assert _lease(db_session, "worker-1") is None  # 失败时阻塞新工作

    retry_call(project.id, call.id, db_session)

    row = db_session.get(R20Call, call.id)
    assert row.status == "retry_pending"
    leased = _lease_retry(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id
    assert leased.status == "leased"


def test_worker_lease_allows_new_work_without_failures(db_session):
    """无失败调用时 _lease 正常租约新工作流。"""
    project = _project(db_session)
    _group(db_session, project)
    stream = _stream(db_session, project)
    db_session.commit()
    leased = _lease(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id
    assert leased.status == "leased"
    assert leased.worker_id == "worker-1"


def test_worker_lease_retry_returns_stream_with_due_retry(db_session):
    """到期重试优先于新工作：存在到期 retry_pending 时 _lease_retry 返回其流。"""
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()
    leased = _lease_retry(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id
    assert leased.status == "leased"
    assert leased.worker_id == "worker-1"


def test_worker_lease_retry_skips_not_yet_due_retry(db_session):
    """未到 next_retry_at 的重试不会返回给 _lease_retry。"""
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = utc_now_naive() + timedelta(seconds=3600)
    db_session.commit()
    assert _lease_retry(db_session, "worker-1") is None


def test_worker_lease_only_touches_active_group(db_session):
    """一次只跑一个组：非 active 组的流不会被租约。"""
    project, stream, call = _ready(db_session, call_status="succeeded")
    _group(
        db_session,
        project,
        condition="crm",
        question="question-2",
        status="pending",
        rank=1,
    )
    other = _stream(db_session, project)
    other.condition = "crm"
    other.question_id = "question-2"
    db_session.commit()

    leased = _lease(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id

    db_session.rollback()
    _lease_retry(db_session, "worker-1")
    stream.status = "completed"
    db_session.commit()
    assert _lease(db_session, "worker-1") is None


def test_worker_lease_retry_scoped_to_active_group(db_session):
    """到期重试也只作用于 active 组。"""
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    _group(
        db_session,
        project,
        condition="crm",
        question="question-2",
        status="pending",
        rank=1,
    )
    other = _stream(db_session, project)
    other.condition = "crm"
    other.question_id = "question-2"
    db_session.commit()

    leased = _lease_retry(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id


def test_start_run_group_flows_frozen_to_running(db_session, monkeypatch):
    """冻结后启动某格：该组 queued、项目 running，冲突时 409。"""
    project = _project(db_session, status="frozen")
    _group(db_session, project, status="pending", rank=0)
    _group(
        db_session,
        project,
        condition="crm",
        question="question-1",
        status="pending",
        rank=1,
    )
    _stream(db_session, project)
    db_session.commit()

    class FakeDataset:
        manifest = {"manifest_sha256": "d" * 64}

    monkeypatch.setattr(
        platform_module, "build_frozen_dataset", lambda *_a, **_k: FakeDataset()
    )
    monkeypatch.setattr(platform_module, "_analysis_code_sha", lambda: "a" * 64)

    started = start_run_group(project.id, "question-1", "nm", db_session)
    assert started["status"] == "queued"
    db_session.refresh(project)
    assert project.status == "running"

    with pytest.raises(HTTPException) as caught:
        start_run_group(project.id, "question-1", "crm", db_session)
    assert caught.value.status_code == 409
    assert "已有实验组在运行" in caught.value.detail

    assert db_session.query(R20Exposure).filter_by(action="start_group").count() == 1


def test_start_run_group_rejects_unknown_or_finished(db_session):
    """未知条件/未知格/已完成项目启动均被拒绝。"""
    project = _project(db_session, status="frozen")
    _group(db_session, project, status="pending")
    db_session.commit()

    with pytest.raises(HTTPException) as caught:
        start_run_group(project.id, "question-1", "bogus", db_session)
    assert caught.value.status_code == 404

    with pytest.raises(HTTPException) as caught:
        start_run_group(project.id, "question-9", "nm", db_session)
    assert caught.value.status_code == 404

    project.status = "completed"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        start_run_group(project.id, "question-1", "nm", db_session)
    assert caught.value.status_code == 409


def test_start_run_group_rejects_paused_project(db_session):
    """项目暂停时不能启动新组。"""
    project = _project(db_session, status="paused")
    _group(db_session, project, status="pending")
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        start_run_group(project.id, "question-1", "nm", db_session)
    assert caught.value.status_code == 409


def test_run_groups_reports_per_group_progress(db_session):
    """GET groups 返回每组状态与进度聚合。"""
    project, stream, call = _ready(db_session)
    _group(
        db_session,
        project,
        condition="crm",
        question="question-1",
        status="pending",
        rank=1,
    )
    call.status = "succeeded"
    call.model_score = 1.0
    db_session.commit()

    rows = run_groups(project.id, db_session)
    assert len(rows) == 2
    nm_row = next(row for row in rows if row["condition"] == "nm")
    assert nm_row["status"] == "running"
    assert nm_row["progress"]["recorded"] == 1
    assert nm_row["progress"]["succeeded"] == 1
    crm_row = next(row for row in rows if row["condition"] == "crm")
    assert crm_row["status"] == "pending"
    assert crm_row["progress"]["recorded"] == 0


def test_run_groups_restores_grid_for_legacy_frozen_project(db_session):
    """冻结项目缺少 grid 行时，读取列表会从既有 streams 无损恢复它们。"""
    project = _project(db_session, status="frozen")
    _stream(db_session, project)
    for condition in ("crm", "arm"):
        db_session.add(
            R20Stream(
                project_id=project.id,
                model_id="test-model",
                question_id="question-1",
                condition=condition,
                trajectory=1,
                order_rank=0,
                status="pending",
            )
        )
    db_session.commit()

    rows = run_groups(project.id, db_session)

    assert [(row["question_id"], row["condition"]) for row in rows] == [
        ("question-1", "nm"),
        ("question-1", "crm"),
        ("question-1", "arm"),
    ]
    assert all(row["status"] == "pending" for row in rows)


def test_worker_lease_refuses_second_stream_while_group_in_flight(db_session):
    """同一 run group 只允许一条流在飞行：已有流 leased 时不再租第二条。"""
    project = _project(db_session)
    _group(db_session, project)
    s1 = _stream(db_session, project)  # trajectory 1, order_rank 0
    s2 = _stream(db_session, project)
    s2.trajectory = 2
    s2.order_rank = 1
    db_session.commit()

    s1.status = "leased"
    s1.worker_id = "worker-1"
    s1.lease_until = utc_now_naive() + timedelta(seconds=60)
    db_session.commit()
    assert _lease(db_session, "worker-2") is None  # 拒绝第二条并行流

    s1.status = "completed"  # 第一条结束
    s1.lease_until = None
    db_session.commit()
    leased = _lease(db_session, "worker-2")
    assert leased is not None
    assert leased.id == s2.id  # 才轮到第二条


def test_worker_lease_retry_waits_while_group_in_flight(db_session):
    """组内有流在飞行时，_lease_retry 也不并行拿流。"""
    project, stream, call = _ready(db_session, call_status="retry_pending")
    call.next_retry_at = utc_now_naive() - timedelta(seconds=1)
    other = _stream(db_session, project)  # 同组另一条流
    other.trajectory = 2
    other.order_rank = 1
    other.status = "leased"
    other.worker_id = "worker-9"
    other.lease_until = utc_now_naive() + timedelta(seconds=60)
    db_session.commit()

    assert _lease_retry(db_session, "worker-1") is None  # 组内有流在飞行

    other.status = "pending"  # 飞行结束
    other.lease_until = None
    db_session.commit()
    leased = _lease_retry(db_session, "worker-1")
    assert leased is not None
    assert leased.id == stream.id
