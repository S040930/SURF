import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.time import utc_now_naive
from app.experiment.memory_study import V3_R2_PROTOCOL_ID
from app.experiment.memory_study.protocol import StudyStatus
from app.models.memory_study import (
    MSCall,
    MSFrameworkInvocation,
    MSMemoryStore,
    MSSchedulerRuntime,
    MSStudy,
)
from app.services.memory_study import MemoryStudyDomainError, MemoryStudyService


def test_run_gate_does_not_require_a_pilot_for_formal(db_session):
    service = MemoryStudyService(db_session)
    service._require_run_gate("pilot")

    db_session.add(
        MSStudy(
            id="development-1",
            protocol_id="saf-memory-framework-v1",
            name="development",
            kind="development",
            status=StudyStatus.completed,
            data_processing_confirmed=True,
            data_manifest_json={},
            config_json={},
            expected_json={},
            progress_json={},
            integrity_status="passed",
            integrity_json={},
            results_embargoed=False,
        )
    )
    db_session.flush()
    service._require_run_gate("formal")


    db_session.add(
        MSStudy(
            id="pilot-1",
            protocol_id="saf-memory-framework-v1",
            name="pilot",
            kind="pilot",
            status=StudyStatus.completed,
            data_processing_confirmed=True,
            data_manifest_json={},
            config_json={},
            expected_json={},
            progress_json={},
            integrity_status="passed",
            integrity_json={},
            results_embargoed=False,
        )
    )
    db_session.flush()
    service._require_run_gate("formal")


def test_new_projects_default_to_v3_r2_and_retired_protocols_are_rejected(
    db_session, monkeypatch
):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_REVISION", "test-revision"
    )

    project = MemoryStudyService(db_session).create_project(
        {
            "name": "development-v3-r2-default",
            "kind": "development",
            "data_processing_confirmed": True,
            "speed_modes": {"gpt-6-luna": "standard"},
        }
    )

    assert project["protocol_id"] == V3_R2_PROTOCOL_ID
    frozen_config = db_session.get(MSStudy, project["id"]).config_json
    assert frozen_config["training_scored"] is False
    assert frozen_config["training_memory_only"] is False
    assert frozen_config["shared_no_memory_baseline"] is False
    assert frozen_config["scoring_context"]["protocol_id"] == V3_R2_PROTOCOL_ID

    with pytest.raises(MemoryStudyDomainError, match="unsupported memory-study protocol"):
        MemoryStudyService(db_session).create_project(
            {
                "name": "retired-v4-protocol",
                "kind": "development",
                "protocol_id": "saf-memory-framework-v4-grading-v2",
                "data_processing_confirmed": True,
            }
        )

async def test_memory_study_routes_are_isolated_and_scores_are_embargoed(
    client, monkeypatch
):
    from app.core import config
    from app.services import memory_study as memory_study_service

    worker_starts: list[str] = []
    preflight_messages: list[tuple[dict[str, str], ...]] = []

    class FakeWorkerManager:
        def start(self, study_id: str) -> str:
            worker_starts.append(study_id)
            return "test-worker"

    class FakePreflightRunner:
        """Deterministic account-backed runner substitute for the API test."""

        def runtime_fingerprint(self):
            return {
                "executable_path": "/test/codex",
                "executable_sha256": "a" * 64,
                "cli_version": "codex-test-1",
                "sandbox": "read-only",
                "ephemeral": True,
                "ignore_user_config": True,
                "ignore_rules": True,
                "output_schema": True,
                "prompt_envelope_version": "test-v1",
            }

        def run(self, **kwargs):
            preflight_messages.append(kwargs["messages"])
            return SimpleNamespace(
                value={
                    "ok": True,
                    "probe": memory_study_service.PREFLIGHT_PROBE,
                },
                latency_ms=1,
                raw_json='{"ok":true,"probe":"memory-study-preflight-v1"}',
            )

    monkeypatch.setattr(
        memory_study_service,
        "memory_study_worker_manager",
        FakeWorkerManager(),
    )
    monkeypatch.setattr(memory_study_service, "CodexExecRunner", FakePreflightRunner)

    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_REVISION", "test-revision"
    )
    audit = await client.get("/api/memory-study/audit?kind=development")
    assert audit.status_code == 200
    body = audit.json()
    assert body["ready"] is True
    assert body["protocol"] == V3_R2_PROTOCOL_ID
    assert len(body["selected_questions"]) == 2

    created = await client.post(
        "/api/memory-study/projects",
        json={
            "name": "memory-study-api-test",
            "kind": "development",
            "data_processing_confirmed": True,
            "speed_modes": {"gpt-6-luna": "fast"},
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["status"] == "ready"
    assert project["config"].get("token_limit") is None
    bound_models = {
        item["model"]: item["speed_mode"] for item in project["config"]["models"]
    }
    assert bound_models == {"gpt-6-luna": "fast"}
    assert project["expected"]["score_calls"] == 70
    assert project["expected"]["memory_write_calls"] == 160
    assert project["config"]["order_variants"] == ["original"]
    assert project["config"]["repeats"] == [1]

    stores = await client.get(
        f"/api/memory-study/projects/{project['id']}/memory-stores"
    )
    assert stores.status_code == 200
    store_items = stores.json()
    assert len(store_items) == 12
    assert all(
        item["status"] == "completed"
        for item in store_items
        if item["framework"] == "retrieval"
    )
    assert (
        sum(
            item["committed_count"]
            for item in store_items
            if item["framework"] == "retrieval"
        )
        == 80
    )

    calls = await client.get(
        f"/api/memory-study/projects/{project['id']}/calls?kind=score&limit=2"
    )
    assert calls.status_code == 200
    assert len(calls.json()["items"]) == 2
    assert calls.json()["items"][0]["manual_score"] is None
    assert calls.json()["items"][0]["model_score"] is None

    frozen = await client.post(f"/api/memory-study/projects/{project['id']}/freeze")
    assert frozen.status_code == 200
    preflight = await client.post(
        f"/api/memory-study/projects/{project['id']}/preflight"
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["preflight"]["status"] == "passed"
    assert len(preflight_messages) == 1
    for messages in preflight_messages:
        user_input = next(
            item["content"] for item in messages if item["role"] == "user"
        )
        assert (
            json.loads(user_input)["probe_token"]
            == memory_study_service.PREFLIGHT_PROBE
        )
        system_instruction = next(
            item["content"] for item in messages if item["role"] == "system"
        )
        assert "copy the input probe_token exactly" in system_instruction
    response = await client.post(f"/api/memory-study/projects/{project['id']}/start")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert worker_starts == [project["id"]]


def test_formal_v3_r2_materializes_seven_conditions_and_frozen_counts(
    db_session, monkeypatch
):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_REVISION", "test-revision"
    )
    project = MemoryStudyService(db_session).create_project(
        {
            "name": "formal-v3-seven-conditions",
            "kind": "formal",
            "data_processing_confirmed": True,
            "speed_modes": {"gpt-6-luna": "standard"},
            "feedback_waves": ["full", "no_feedback"],
        }
    )
    assert project["protocol_id"] == V3_R2_PROTOCOL_ID
    assert len(project["config"]["models"]) == 1
    assert {
        key: project["config"]["models"][0][key]
        for key in ("model", "reasoning_effort", "speed_mode", "timeout_seconds")
    } == {
        "model": "gpt-6-luna",
        "reasoning_effort": "medium",
        "speed_mode": "standard",
        "timeout_seconds": 120,
    }
    assert project["expected"]["score_calls"] == 4_200
    assert project["expected"]["memory_write_calls"] == 2_880
    assert project["expected"]["materialized_memory_stores"] == 72
    assert project["expected"]["deterministic_retrieval_materializations"] == 24
    stores = list(
        db_session.scalars(
            select(MSMemoryStore).where(MSMemoryStore.study_id == project["id"])
        )
    )
    assert len(stores) == 72
    assert len([store for store in stores if store.framework == "retrieval"]) == 24
    assert all(store.status == "pending" for store in stores)
    score_calls = db_session.scalar(
        select(func.count(MSCall.id)).where(
            MSCall.study_id == project["id"], MSCall.kind == "score"
        )
    )
    write_calls = db_session.scalar(
        select(func.count(MSCall.id)).where(
            MSCall.study_id == project["id"], MSCall.kind == "memory_write"
        )
    )
    assert score_calls == 4_200
    assert write_calls == 2_880


def test_create_project_rejects_unknown_speed_mode_model(db_session):
    with pytest.raises(MemoryStudyDomainError, match="unknown model in speed_modes"):
        MemoryStudyService(db_session).create_project(
            {
                "name": "bad-binding",
                "kind": "development",
                "data_processing_confirmed": True,
                "speed_modes": {"gpt-4o": "fast"},
            }
        )


def test_create_project_rejects_terra_binding(db_session):
    with pytest.raises(MemoryStudyDomainError, match="unknown model in speed_modes"):
        MemoryStudyService(db_session).create_project(
            {
                "name": "terra-binding",
                "kind": "development",
                "data_processing_confirmed": True,
                "speed_modes": {"gpt-5.6-terra": "standard"},
            }
        )


def test_terminated_v2_project_is_read_only_but_remains_auditable(db_session):
    class WorkerManager:
        def __init__(self):
            self.terminated: list[str] = []

        def terminate(self, study_id: str) -> None:
            self.terminated.append(study_id)

    manager = WorkerManager()
    study = MSStudy(
        id="legacy-v2",
        protocol_id="saf-memory-framework-v2",
        name="legacy dual model",
        kind="formal",
        status=StudyStatus.running,
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={"models": [{"model": "gpt-6-luna"}, {"model": "gpt-5.6-terra"}]},
        expected_json={"score_calls": 1, "memory_write_calls": 0},
        progress_json={},
        integrity_json={},
    )
    db_session.add(study)
    call = MSCall(
        study_id=study.id,
        model="gpt-5.6-terra",
        question_id="q1",
        condition="no_memory",
        framework="none",
        feedback_mode="full",
        order_variant="order_1",
        kind="score",
        answer_id="a1",
        repeat=1,
        status="pending",
    )
    db_session.add(call)
    db_session.commit()

    service = MemoryStudyService(db_session, worker_manager=manager)
    terminated = service.terminate(study.id)
    assert terminated["status"] == StudyStatus.terminated
    assert manager.terminated == [study.id]
    calls = service.list_calls(study.id, limit=10)
    assert calls["items"][0]["model"] == "gpt-5.6-terra"
    assert calls["items"][0]["status"] == "cancelled"
    with pytest.raises(MemoryStudyDomainError, match="只读审计"):
        service.delete_project(study.id)
    with pytest.raises(MemoryStudyDomainError, match="terminal studies"):
        service.retry_call(study.id, call.id)


async def test_create_project_duplicate_name_returns_conflict(client):
    payload = {
        "name": "duplicate-name",
        "kind": "development",
        "data_processing_confirmed": True,
    }
    first = await client.post("/api/memory-study/projects", json=payload)
    assert first.status_code == 201, first.text

    second = await client.post("/api/memory-study/projects", json=payload)
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "conflict"
    assert "already exists" in second.json()["detail"]["message"]


async def test_memory_study_does_not_add_legacy_routes(client):
    schema = (await client.get("/openapi.json")).json()
    paths = set(schema["paths"])
    assert "/api/memory-study/audit" in paths
    assert "/api/memory-study/projects/{study_id}" in paths
    assert "/api/memory-study/projects/{study_id}/memory-stores/{store_id}" in paths
    assert not any(path.startswith("/api/r20") for path in paths)


async def test_runtime_reports_worker_state_and_blocked_reason(client, db_session):
    """The runtime payload distinguishes online/offline and carries原因字段."""
    offline = (await client.get("/api/memory-study/runtime")).json()
    assert offline["online"] is False
    assert offline["blocked_reason"] is None
    assert offline["state"] == "offline"

    db_session.add(
        MSSchedulerRuntime(
            id=1,
            owner_id="w1",
            status="online",
            max_subprocesses=2,
            active_subprocesses=2,
            slots_json={"slot-0": {"model": "m", "study_id": "s"}},
            heartbeat_at=utc_now_naive(),
            lease_until=utc_now_naive() + timedelta(seconds=600),
        )
    )
    db_session.commit()
    online = (await client.get("/api/memory-study/runtime")).json()
    assert online["online"] is True
    assert online["state"] == "online"
    assert online["reason"] is None


async def test_runtime_reports_recovering_after_crash_left_stale_lease(
    client, db_session
):
    """A stale online lease + running study surfaces as recovering, not failure.

    The worker crashed without releasing the lease; the OS supervision loop
    restarts it within seconds.  Showing "not connected" here made operators
    think the study died while it was self-healing.
    """
    db_session.add(
        MSStudy(
            id="recover-1",
            protocol_id="p",
            name="recover",
            kind="formal",
            status=StudyStatus.running,
            data_processing_confirmed=True,
            data_manifest_json={},
            config_json={},
            expected_json={},
            progress_json={},
            integrity_json={},
        )
    )
    db_session.add(
        MSSchedulerRuntime(
            id=1,
            owner_id="w-crashed",
            status="online",
            max_subprocesses=2,
            active_subprocesses=1,
            slots_json={"slot-0": {"model": "m", "study_id": "recover-1"}},
            heartbeat_at=utc_now_naive() - timedelta(seconds=60),
            lease_until=utc_now_naive() + timedelta(seconds=540),
        )
    )
    db_session.commit()

    payload = (await client.get("/api/memory-study/runtime")).json()
    assert payload["online"] is True, "lease is still live; worker healthy"

    # Simulate the lease expiring while the row still says online (crash
    # residue) and a study remains marked running.
    runtime = db_session.get(MSSchedulerRuntime, 1)
    runtime.lease_until = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()

    stale = (await client.get("/api/memory-study/runtime")).json()
    assert stale["online"] is False
    assert stale["state"] == "recovering"
    assert "resume automatically" in stale["reason"]


async def test_runtime_stays_offline_without_running_studies(client, db_session):
    """An expired stale lease with no running study is a plain offline state."""
    db_session.add(
        MSSchedulerRuntime(
            id=1,
            owner_id="w-crashed",
            status="online",
            max_subprocesses=2,
            active_subprocesses=0,
            slots_json={},
            heartbeat_at=utc_now_naive() - timedelta(seconds=700),
            lease_until=utc_now_naive() - timedelta(seconds=100),
        )
    )
    db_session.commit()

    payload = (await client.get("/api/memory-study/runtime")).json()
    assert payload["online"] is False
    assert payload["state"] == "offline"
    assert payload["reason"] == "memory-study worker is not connected"


async def test_retry_failed_requeues_every_failed_call(client, db_session):
    db_session.add(
        MSStudy(
            id="retry-1",
            protocol_id="p",
            name="retry",
            kind="pilot",
            status=StudyStatus.attention_required,
            data_processing_confirmed=True,
            data_manifest_json={},
            config_json={},
            expected_json={},
            progress_json={},
            integrity_json={},
        )
    )
    db_session.add(
        MSCall(
            study_id="retry-1",
            model="m",
            question_id="q",
            condition="no_memory",
            framework="none",
            feedback_mode="full",
            order_variant="order_1",
            kind="score",
            answer_id="a",
            repeat=0,
            status="failed",
            failure_code="execution_error",
        )
    )
    db_session.commit()

    response = await client.post("/api/memory-study/projects/retry-1/retry-failed")
    assert response.status_code == 200, response.text
    assert response.json()["requeued"] == 1
    db_session.expire_all()
    call = db_session.scalar(select(MSCall))
    assert call.status == "pending"
    assert call.failure_code is None


def _failed_store_study(db_session, *, orphan_count=1):
    """A study stuck in ``attention_required`` with a terminally failed store.

    This is the state the worker leaves behind after a write exhausts its
    retries: the store is failed, its remaining writes were terminated by the
    sweep with ``store_failed``, and the study is waiting for a manual retry.
    """
    study = MSStudy(
        id="retry-store-1",
        protocol_id="saf-memory-framework-v1",
        name="retry-store",
        kind="pilot",
        status=StudyStatus.attention_required,
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={},
        expected_json={},
        progress_json={},
        integrity_json={},
        error_summary="1 call(s) failed; manual retry required",
    )
    db_session.add(study)
    store = MSMemoryStore(
        study_id=study.id,
        model="gpt-6-luna",
        question_id="q1",
        framework="mem0",
        condition="mem0_full",
        feedback_mode="full",
        order_variant="order_1",
        status="failed",
        committed_count=0,
        error_summary="framework ingest exited with 1",
    )
    db_session.add(store)
    db_session.flush()
    calls = []
    for index in range(orphan_count + 1):  # +1: the write that actually failed
        call = MSCall(
            study_id=study.id,
            memory_store_id=store.id,
            model="gpt-6-luna",
            question_id="q1",
            condition="mem0_full",
            framework="mem0",
            feedback_mode="full",
            order_variant="order_1",
            kind="memory_write",
            answer_id=f"a{index}",
            repeat=0,
            status="failed",
            failure_code=("runner_execution_error" if index == 0 else "store_failed"),
            attempt_count=1,
        )
        db_session.add(call)
        calls.append(call)
    db_session.commit()
    return study, store, calls


def test_retry_all_failed_requeues_store_failed_rows(db_session):
    """The store_failed orphans must be reachable by the batch retry."""
    study, store, calls = _failed_store_study(db_session, orphan_count=2)
    service = MemoryStudyService(db_session)

    result = service.retry_all_failed(study.id)

    assert result["requeued"] == 3
    for call in calls:
        db_session.refresh(call)
        assert call.status == "pending"
        assert call.failure_code is None
        assert call.lease_until is None
    db_session.refresh(store)
    # The whole stream is re-opened, so it must be training again -- and the
    # committed count has to fall back with it, or the completion check would
    # be satisfied by the stale value.
    assert store.status == "pending"
    assert store.committed_count == 0
    assert store.error_summary is None
    db_session.refresh(study)
    assert study.status == StudyStatus.paused
    assert study.error_summary is None


def test_retry_call_rejects_later_failed_call_before_current_one(db_session):
    """Manual retry must follow the same call order as execution."""
    study, store, calls = _failed_store_study(db_session, orphan_count=1)
    service = MemoryStudyService(db_session)

    with pytest.raises(MemoryStudyDomainError, match="must be retried before"):
        service.retry_call(study.id, calls[1].id)


def test_resume_rejects_a_historically_parallel_study(db_session):
    """A legacy two-slot run cannot be presented as a strict serial run."""
    study, _store, calls = _failed_store_study(db_session, orphan_count=1)
    calls[0].slot_id = "slot-0"
    calls[1].slot_id = "slot-1"
    db_session.commit()
    service = MemoryStudyService(db_session)

    with pytest.raises(MemoryStudyDomainError, match="multiple slots"):
        service.resume(study.id)

    audit = service.audit_project(study.id)
    assert audit["checks"]["strict_serial_execution"] is False


def test_retry_call_rejects_calls_that_are_not_failed(db_session):
    """Locked contract: only failed rows may be retried by hand."""
    study, store, calls = _failed_store_study(db_session, orphan_count=0)
    calls[0].status = "pending"
    db_session.commit()

    with pytest.raises(MemoryStudyDomainError, match="only failed calls"):
        MemoryStudyService(db_session).retry_call(study.id, calls[0].id)


def test_audit_reads_the_framework_invocation_ledger(db_session):
    """Why the ledger has to survive a failed write.

    ``audit_project`` reports ``no_failed_framework_invocations`` straight off
    this table, and ``passed`` is what unseals the results.  Erasing the rows on
    the failure path therefore did not just lose a diagnosis: it let a run in
    which every internal request had failed look clean enough to pass.
    """
    study, _store, calls = _failed_store_study(db_session, orphan_count=0)
    service = MemoryStudyService(db_session)

    blind = service.audit_project(study.id)
    assert blind["checks"]["no_failed_framework_invocations"] is True
    assert blind["checks"]["all_calls_terminal"] is True

    db_session.add(
        MSFrameworkInvocation(
            call_id=calls[0].id,
            attempt_number=1,
            sequence_number=1,
            framework="mem0",
            phase="mem0.generate_response",
            transport="codex_exec",
            requested_model="gpt-6-luna",
            reasoning_effort="medium",
            cli_fingerprint_json={},
            request_sha256="0" * 64,
            status="failed",
            error_type="RunnerExecutionError",
            error_message="codex exec exited with 1; stderr: invalid_json_schema",
            started_at=utc_now_naive(),
            completed_at=utc_now_naive(),
        )
    )
    db_session.commit()

    seeing = service.audit_project(study.id)
    assert seeing["checks"]["no_failed_framework_invocations"] is False
    # The other terminal-state checks are unaffected: the failure is a genuine
    # one, reported through the channel that was designed for it.
    assert seeing["checks"]["all_calls_terminal"] is True
