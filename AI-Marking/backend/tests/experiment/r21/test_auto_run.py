from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.core.time import utc_now_naive
from app.experiment.r21.codex_runner import CodexResult, RunnerExecutionError
from app.experiment.r21.executor import CallSpec, _complete_stream, run_one
from app.experiment.r21.protocol import expected_question_calls
from app.models.r21 import (
    R21Call,
    R21CallAttempt,
    R21Project,
    R21RunGroup,
    R21RunnerRuntime,
    R21Stream,
)
from app.models.r23 import (
    R23CallAttempt,
    R23Event,
    R23ModelBinding,
    R23Project,
    R23RunGroup,
    R23UniqueEvaluation,
)
from app.services.r21 import R21DomainError, R21Service


def _project(*, status: str = "frozen", name: str | None = None) -> R21Project:
    return R21Project(
        id=str(uuid4()),
        protocol_id="r21-test",
        name=name or f"project-{uuid4()}",
        kind="pilot_run",
        status=status,
        runner_config_id="runner",
        runner_config_json={"model": "m"},
        runner_config_sha256="r" * 64,
        runner_runtime_json={
            "model": "m",
            "reasoning_effort": "medium",
            "cli_version": "codex test",
            "prompt_envelope_version": "r21-codex-exec-v1",
        },
        prompt_version_id="prompt",
        prompt_version_name="prompt",
        prompt_templates_json={
            "scoring": "score",
            "crm_update": "crm",
            "arm_update": "arm",
        },
        prompt_version_sha256="p" * 64,
        manifest_json={"expected_calls": 2},
        manifest_sha256="m" * 64,
        data_sha256="data",
        analysis_code_sha256="analysis",
    )


def _group(project: R21Project, rank: int, status: str = "pending") -> R21RunGroup:
    return R21RunGroup(
        project_id=project.id,
        question_id=f"q-{rank // 3}",
        condition=("nm", "crm", "arm")[rank % 3],
        status=status,
        order_rank=rank,
        expected_calls=1,
    )


def _call(project: R21Project, status: str = "failed_terminal") -> R21Call:
    return R21Call(
        project_id=project.id,
        kind="test_score",
        status=status,
        condition="nm",
        question_id="q-0",
        answer_id="answer",
        group_id="group",
        trajectory=1,
        history_count=20,
        repeat=1,
        input_json={},
        teacher_score=1,
        max_score=10,
        failure_reason="failed",
    )


def test_protocol_call_counts_remain_frozen():
    assert expected_question_calls(20, 5, 1) == 255
    assert expected_question_calls(60, 15, 2) == 720
    assert 2 * 255 == 510
    assert 8 * 720 == 5760


def test_start_project_queues_only_first_group(db_session, monkeypatch):
    project = _project()
    first, second = _group(project, 0), _group(project, 1)
    db_session.add_all([project, first, second])
    db_session.commit()
    monkeypatch.setattr(
        "app.services.r21.build_frozen_dataset",
        lambda *_: SimpleNamespace(manifest={"manifest_sha256": "data"}),
    )
    monkeypatch.setattr("app.services.r21._analysis_code_sha", lambda: "analysis")

    result = R21Service(db_session).start_project(project.id)

    assert project.status == "running"
    assert result["status"] == "waiting_for_runner"
    assert first.status == "queued"
    assert second.status == "pending"


def test_completed_group_automatically_queues_next_group(db_session):
    project = _project(status="running")
    project.started_at = utc_now_naive()
    first, second = _group(project, 0, "running"), _group(project, 1)
    stream = R21Stream(
        project_id=project.id,
        question_id=first.question_id,
        condition=first.condition,
        trajectory=1,
        order_rank=0,
        status="leased",
        worker_id="worker",
    )
    db_session.add_all([project, first, second, stream])
    db_session.commit()

    _complete_stream(db_session, project, stream)

    assert first.status == "completed"
    assert second.status == "queued"
    assert project.status == "running"


def test_runner_lease_is_singleton_and_stale_takeover_recovers_work(db_session):
    service = R21Service(db_session)
    assert service.acquire_runner_lease("worker-a", {"cli_version": "a"}) is True
    assert service.acquire_runner_lease("worker-b", {"cli_version": "b"}) is False

    project = _project(status="running")
    group = _group(project, 0, "running")
    stream = R21Stream(
        project_id=project.id,
        question_id="q-0",
        condition="nm",
        trajectory=1,
        order_rank=0,
        status="leased",
        worker_id="worker-a",
    )
    call = _call(project, status="pending")
    db_session.add_all([project, group, stream, call])
    runtime = db_session.get(R21RunnerRuntime, 1)
    runtime.heartbeat_at = utc_now_naive() - timedelta(seconds=31)
    db_session.commit()

    assert service.acquire_runner_lease("worker-b", {"cli_version": "b"}) is True
    assert runtime.worker_id == "worker-b"
    assert stream.status == "pending"
    assert stream.worker_id is None
    assert call.status == "retry_pending"
    assert service.runtime_status()["runner_online"] is True


def test_stale_takeover_surfaces_interrupted_r23_call_for_manual_retry(db_session):
    service = R21Service(db_session)
    assert service.acquire_runner_lease("worker-a", {"cli_version": "a"}) is True

    project = R23Project(
        id=str(uuid4()),
        protocol_id="r23-test",
        name=f"r23-project-{uuid4()}",
        kind="pilot_run",
        status="running",
        rubric_version_id="rubric",
        data_processing_confirmed=True,
        data_status_json={},
        manifest_json={},
    )
    binding = R23ModelBinding(
        id=str(uuid4()),
        project_id=project.id,
        runner_config_id="runner",
        position=1,
        model="model-a",
        config_sha256="c" * 64,
        frozen_runtime_json={
            "reasoning_effort": "medium",
            "speed_mode": "fast",
            "timeout_seconds": 60,
        },
    )
    group = R23RunGroup(
        project_id=project.id,
        model_binding_id=binding.id,
        dimension="content",
        run_index=0,
        status="running",
        order_rank=1,
        expected_calls=1,
        completed_calls=0,
    )
    db_session.add_all([project, binding, group])
    db_session.flush()
    leased_at = utc_now_naive()
    evaluation = R23UniqueEvaluation(
        project_id=project.id,
        model_binding_id=binding.id,
        run_group_id=group.id,
        input_sha256="i" * 64,
        run_index=0,
        status="leased",
        prompt_text="prompt",
        essay_text="essay",
        attempt_count=1,
        worker_id="worker-a",
        lease_until=leased_at + timedelta(seconds=660),
    )
    db_session.add(evaluation)
    runtime = db_session.get(R21RunnerRuntime, 1)
    runtime.heartbeat_at = utc_now_naive() - timedelta(seconds=31)
    db_session.commit()

    assert service.acquire_runner_lease("worker-b", {"cli_version": "b"}) is True

    assert evaluation.status == "attention_required"
    assert evaluation.failure_code == "interrupted"
    assert evaluation.worker_id is None
    assert evaluation.lease_until is None
    assert group.status == "blocked"
    assert project.status == "attention_required"
    attempt = db_session.scalar(
        select(R23CallAttempt).where(R23CallAttempt.evaluation_id == evaluation.id)
    )
    assert attempt is not None
    assert attempt.attempt_number == 1
    assert attempt.status == "failed"
    assert attempt.error_code == "interrupted"
    assert attempt.speed_mode == "fast"
    assert attempt.started_at == leased_at
    event = db_session.scalar(select(R23Event).where(R23Event.project_id == project.id))
    assert event is not None
    assert event.detail_json["call_id"] == evaluation.id


def test_retry_continues_project_and_uses_next_attempt_number(db_session, monkeypatch):
    project = _project(status="attention_required")
    project.started_at = utc_now_naive()
    group = _group(project, 0, "blocked")
    stream = R21Stream(
        project_id=project.id,
        question_id="q-0",
        condition="nm",
        trajectory=1,
        order_rank=0,
        status="blocked",
    )
    call = _call(project)
    db_session.add_all([project, group, stream, call])
    db_session.flush()
    for attempt_number in (1, 2):
        db_session.add(
            R21CallAttempt(
                call_id=call.id,
                attempt_number=attempt_number,
                requested_model="m",
                reasoning_effort="medium",
                latency_ms=1,
                status="failed",
                started_at=utc_now_naive(),
            )
        )
    db_session.commit()

    R21Service(db_session).retry_call(project.id, call.id)
    assert project.status == "running"
    assert group.status == "running"
    assert stream.status == "pending"

    spec = CallSpec("test_score", "q-0", "nm", 1, "answer", 20, 1)
    record = SimpleNamespace(group_id="group", teacher_score=1, max_score=10)
    monkeypatch.setattr("app.experiment.r21.executor.next_call", lambda *_: spec)
    monkeypatch.setattr(
        "app.experiment.r21.executor._input_and_messages",
        lambda *_: ({}, ({"role": "user", "content": "{}"},), record, None),
    )

    class Runner:
        def assert_matches(self, _runtime):
            return None

        def run(self, **_kwargs):
            return CodexResult(
                value={"score": 1.0, "feedback": "ok"},
                latency_ms=10,
                exit_code=0,
                stderr_excerpt="",
            )

    assert run_one(db_session, worker_id="worker", runner_factory=Runner) is True
    attempts = sorted(
        db_session.scalars(
            select(R21CallAttempt.attempt_number).where(
                R21CallAttempt.call_id == call.id
            )
        )
    )
    assert attempts == [1, 2, 3]
    assert call.status == "succeeded"


def test_terminal_failure_requires_attention(db_session, monkeypatch):
    project = _project(status="running")
    project.started_at = utc_now_naive()
    group = _group(project, 0, "running")
    stream = R21Stream(
        project_id=project.id,
        question_id="q-0",
        condition="nm",
        trajectory=1,
        order_rank=0,
        status="pending",
    )
    db_session.add_all([project, group, stream])
    db_session.commit()
    spec = CallSpec("test_score", "q-0", "nm", 1, "answer", 20, 1)
    record = SimpleNamespace(group_id="group", teacher_score=1, max_score=10)
    monkeypatch.setattr("app.experiment.r21.executor.next_call", lambda *_: spec)
    monkeypatch.setattr(
        "app.experiment.r21.executor._input_and_messages",
        lambda *_: ({}, ({"role": "user", "content": "{}"},), record, None),
    )

    class FailingRunner:
        def assert_matches(self, _runtime):
            return None

        def run(self, **_kwargs):
            raise RunnerExecutionError("broken")

    assert (
        run_one(db_session, worker_id="worker", runner_factory=FailingRunner) is False
    )
    assert project.status == "attention_required"
    assert group.status == "blocked"
    assert stream.status == "blocked"


def test_start_project_rejects_second_running_project(db_session, monkeypatch):
    running = _project(status="running", name="running")
    frozen = _project(status="frozen", name="frozen")
    db_session.add_all([running, frozen, _group(frozen, 0)])
    db_session.commit()
    monkeypatch.setattr(
        "app.services.r21.build_frozen_dataset",
        lambda *_: SimpleNamespace(manifest={"manifest_sha256": "data"}),
    )
    monkeypatch.setattr("app.services.r21._analysis_code_sha", lambda: "analysis")

    try:
        R21Service(db_session).start_project(frozen.id)
    except R21DomainError as exc:
        assert exc.code == "conflict"
    else:
        raise AssertionError("second running project was accepted")


async def test_start_project_rest_requires_one_confirmation(
    client, db_session, monkeypatch
):
    project = _project()
    db_session.add_all([project, _group(project, 0), _group(project, 1)])
    db_session.commit()
    monkeypatch.setattr(
        "app.services.r21.build_frozen_dataset",
        lambda *_: SimpleNamespace(manifest={"manifest_sha256": "data"}),
    )
    monkeypatch.setattr("app.services.r21._analysis_code_sha", lambda: "analysis")

    rejected = await client.post(f"/api/r21/projects/{project.id}/start")
    assert rejected.status_code == 422
    accepted = await client.post(f"/api/r21/projects/{project.id}/start?confirm=true")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "waiting_for_runner"


def test_start_project_mcp_tool_requires_confirmation(monkeypatch):
    from app import mcp_server

    monkeypatch.setattr(mcp_server, "_svc_call", lambda *_args, **_kwargs: {"ok": True})
    rejected = mcp_server.r21_start_project("project")
    assert rejected["error"]["code"] == "validation"
    assert mcp_server.r21_start_project("project", confirm=True) == {"ok": True}


def test_public_contract_exposes_project_start_not_group_start():
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/r21/projects/{project_id}/start" in paths
    assert (
        "/api/r21/projects/{project_id}/groups/{question_id}/{condition}/start"
        not in paths
    )
