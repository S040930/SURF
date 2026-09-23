"""Worker queue semantics: claim locking, lease recovery, retry policy."""

import json
from datetime import timedelta

import pytest

from app.core.time import utc_now_naive
from app.experiment.memory_study import worker as worker_module
from app.experiment.memory_study.memory import MemoryEvidenceError, RetrievedCase
from app.experiment.memory_study.protocol import CallStatus, StudyStatus
from app.experiment.memory_study.worker import (
    MemoryStudyWorker,
    _minimal_memory_case,
    claim_next_call,
    reclaim_expired_calls,
    recompute_store_progress,
)
from app.models.memory_study import MSStudy

LUNA = "gpt-6-luna"
TERRA = "gpt-5.6-terra"


@pytest.fixture(autouse=True)
def _worker_sessions(db_session, db_session_factory, monkeypatch):
    """Point the worker's SessionLocal at the test database."""
    from app.db import session as db_session_module

    monkeypatch.setattr(worker_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(db_session_module, "SessionLocal", db_session_factory)


@pytest.fixture
def running_study(db_session):
    study = MSStudy(
        id="study-1",
        protocol_id="p",
        name="pilot",
        kind="pilot",
        status=StudyStatus.running,
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={"models": [{"model": LUNA}, {"model": TERRA}]},
        expected_json={},
        progress_json={},
        integrity_json={},
    )
    db_session.add(study)
    # Score calls are gated on every memory store being completed; add one
    # completed store per model so the fixture's score calls are claimable.
    for model in (LUNA, TERRA):
        db_session.add(
            worker_module.MSMemoryStore(
                study_id=study.id,
                model=model,
                question_id="q1",
                framework="none",
                condition="mem0_full",
                feedback_mode="full",
                order_variant="order_1",
                status="completed",
            )
        )
    db_session.commit()
    return study


def _add_call(
    db_session,
    study_id,
    *,
    model,
    kind="score",
    status="pending",
    answer_id="a1",
    question_id=None,
    **kw,
):
    call = worker_module.MSCall(
        study_id=study_id,
        model=model,
        question_id=question_id or "q1",
        condition=kw.pop("condition", "no_memory" if kind == "score" else "mem0_full"),
        framework="none",
        feedback_mode="full",
        order_variant="order_1",
        kind=kind,
        answer_id=answer_id,
        repeat=0,
        status=status,
        **kw,
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_claims_global_order_and_blocks_inflight_call(db_session, running_study):
    luna_call = _add_call(db_session, running_study.id, model=LUNA)
    terra_call = _add_call(db_session, running_study.id, model=TERRA)

    claimed = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w1",
        slot_id="slot-0",
    )
    assert claimed is not None
    assert claimed.id == luna_call.id
    assert claimed.status == CallStatus.leased
    # A later call cannot bypass the current in-flight call, regardless of
    # which frozen model owns it.
    assert (
        claim_next_call(db_session, running_study.id, worker_id="w2", slot_id="slot-0")
        is None
    )

    db_session.refresh(luna_call)
    luna_call.status = CallStatus.succeeded
    db_session.commit()
    terra = claim_next_call(
        db_session, running_study.id, worker_id="w1", slot_id="slot-0"
    )
    assert terra is not None
    assert terra.id == terra_call.id


def test_worker_rejects_legacy_protocol_before_claiming_calls(
    db_session, running_study
):
    running_study.protocol_id = "saf-memory-framework-v2"
    running_study.config_json = {"models": [{"model": LUNA}]}
    db_session.commit()

    with pytest.raises(RuntimeError, match="supported Luna-only V3 protocol"):
        MemoryStudyWorker(runner_factory=lambda: _UnusedRunner()).run(
            running_study.id, worker_id="legacy-worker"
        )


def test_v3_claim_rejects_a_non_luna_model(db_session, running_study):
    running_study.protocol_id = "saf-memory-framework-v3"
    running_study.config_json = {"models": [{"model": LUNA}]}
    db_session.commit()
    terra_call = _add_call(db_session, running_study.id, model=TERRA)

    assert (
        claim_next_call(
            db_session,
            running_study.id,
            worker_id="terra-worker",
            slot_id="slot-terra",
            model=TERRA,
        )
        is None
    )
    db_session.refresh(terra_call)
    assert terra_call.status == "pending"


def test_model_slots_claim_independently_but_remain_serial(db_session, running_study):
    """Luna and Terra may each hold one call, never two for one model."""
    luna_first = _add_call(db_session, running_study.id, model=LUNA)
    luna_second = _add_call(db_session, running_study.id, model=LUNA, answer_id="a2")
    terra_first = _add_call(db_session, running_study.id, model=TERRA)

    luna = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w-luna",
        slot_id="slot-luna",
        model=LUNA,
    )
    terra = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w-terra",
        slot_id="slot-terra",
        model=TERRA,
    )
    assert luna is not None and luna.id == luna_first.id
    assert terra is not None and terra.id == terra_first.id
    assert (
        claim_next_call(
            db_session,
            running_study.id,
            worker_id="w-other",
            slot_id="slot-luna",
            model=LUNA,
        )
        is None
    )
    db_session.refresh(luna_second)
    assert luna_second.status == CallStatus.pending


def test_claim_requeues_expired_lease(db_session, running_study):
    call = _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        status="running",
        attempt_count=1,
        lease_until=utc_now_naive() - timedelta(seconds=1),
    )
    reclaimed = reclaim_expired_calls(db_session, running_study.id)
    assert reclaimed == 1
    db_session.refresh(call)
    assert call.status == CallStatus.pending
    assert call.lease_until is None
    attempts = (
        db_session.query(worker_module.MSCallAttempt)
        .filter(worker_module.MSCallAttempt.call_id == call.id)
        .all()
    )
    assert [a.status for a in attempts] == ["failed"]
    assert attempts[0].error_type == "lease_expired"


def test_cleanup_stale_attempt_dirs_preserves_committed_native_artifacts(
    tmp_path, monkeypatch
):
    """Hot restart may remove staging dirs, never a committed Mem0 sidecar."""
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    stream = tmp_path / "study-1" / "memory" / "gpt-6-luna" / "q1"
    committed = stream / "native-artifacts"
    committed.mkdir(parents=True)
    (committed / "0-vector").write_text("committed", encoding="utf-8")
    stale = stream / "native-crashed-attempt"
    stale.mkdir()
    (stale / "partial").write_text("discard", encoding="utf-8")

    worker_module._cleanup_stale_attempt_dirs("study-1")

    assert (committed / "0-vector").read_text(encoding="utf-8") == "committed"
    assert not stale.exists()


def test_empty_store_clears_framework_initialization_residue(
    db_session, running_study, tmp_path, monkeypatch
):
    """A first A-MEM score may create files before the first memory commit."""
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    store = _add_store(db_session, running_study.id, model=TERRA, framework="amem")
    root = (
        tmp_path / running_study.id / "memory" / TERRA / "q2" / "amem_full" / "order_1"
    )
    root.mkdir(parents=True)
    (root / "chroma").mkdir()
    (root / "chroma" / "chroma.sqlite3").write_text("residue", encoding="utf-8")
    store.snapshot_path = str(root / "snapshot.json")
    db_session.commit()

    worker_module._prepare_empty_store_for_write(store)

    assert list(root.iterdir()) == []
    assert worker_module._snapshot_is_valid(store)


def test_isolated_store_read_initializes_missing_empty_stream_parent(
    db_session, running_study, tmp_path, monkeypatch
):
    """The first score may read an empty formal-v3 stream before any write."""
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    store = _add_store(db_session, running_study.id, model=LUNA, framework="retrieval")
    root = (
        tmp_path
        / running_study.id
        / "memory"
        / LUNA
        / "q-missing"
        / "retrieval_full"
        / "order_1"
    )
    store.snapshot_path = str(root / "snapshot.json")
    db_session.commit()
    assert not root.parent.exists()

    with worker_module._isolated_store_read(store):
        isolated_path = worker_module.Path(store.snapshot_path)
        assert isolated_path.name == "snapshot.json"
        assert isolated_path.parent.is_dir()

    assert store.snapshot_path == str(root / "snapshot.json")
    assert root.parent.is_dir()
    assert not root.exists()


def test_cleanup_restores_publish_backup_after_interrupted_promotion(
    db_session, running_study, tmp_path, monkeypatch
):
    """A crash between directory moves must preserve the previous commit."""
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    snapshot = {"framework": "mem0", "history_answer_ids": ["a1"]}
    store = _add_store(
        db_session,
        running_study.id,
        model=LUNA,
        status="completed",
        snapshot_json=snapshot,
        snapshot_sha256=worker_module.memory_hash(snapshot),
    )
    committed = (
        tmp_path / running_study.id / "memory" / LUNA / "q2" / "mem0_full" / "order_1"
    )
    committed.mkdir(parents=True)
    store.snapshot_path = str(committed / "snapshot.json")
    db_session.commit()
    (committed / "snapshot.json").write_text(
        worker_module._json(snapshot), encoding="utf-8"
    )
    backup = committed.parent / f"publish-{store.id}-crashed"
    backup.mkdir()
    (backup / "snapshot.json").write_text(
        worker_module._json(snapshot), encoding="utf-8"
    )
    (committed / "partial.db").write_text("partial", encoding="utf-8")
    (committed / "snapshot.json").unlink()

    worker_module._cleanup_stale_attempt_dirs(running_study.id)

    assert (committed / "snapshot.json").read_text(
        encoding="utf-8"
    ) == worker_module._json(snapshot)
    assert not (committed / "partial.db").exists()
    assert not backup.exists()


def test_cleanup_skips_attempt_dirs_of_in_flight_calls(
    db_session, running_study, tmp_path, monkeypatch
):
    """A re-entered run() must not delete the staging tree of a live call.

    The 2026-09-18 pilot hit exactly this: a resume while slot threads were
    mid-ingest removed their attempt dirs, failing amem with ENOENT and mem0
    with "attempt to write a readonly database".
    """
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    store = _add_store(db_session, running_study.id, model=LUNA)
    _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="memory_write",
        status=CallStatus.running,
        memory_store_id=store.id,
    )
    condition = tmp_path / running_study.id / "memory" / LUNA / "q2" / "mem0_full"
    committed = condition / "order_1"
    committed.mkdir(parents=True)
    store.snapshot_path = str(committed / "snapshot.json")
    db_session.commit()
    live_attempt = condition / f"attempt-{store.id}-liverun" / "store"
    (live_attempt / "embeddings").mkdir(parents=True)
    (live_attempt / "embeddings" / "vector.npy").write_text("live", encoding="utf-8")
    nested = live_attempt / "native-construction"
    nested.mkdir()
    (nested / "partial").write_text("live", encoding="utf-8")
    stale_attempt = condition / "attempt-999999-crashed"
    stale_attempt.mkdir()
    (stale_attempt / "orphan").write_text("stale", encoding="utf-8")

    worker_module._cleanup_stale_attempt_dirs(running_study.id)

    assert (live_attempt / "embeddings" / "vector.npy").read_text(
        encoding="utf-8"
    ) == "live"
    assert (nested / "partial").read_text(encoding="utf-8") == "live"
    assert not stale_attempt.exists()


def test_cleanup_leaves_publish_backup_of_in_flight_store(
    db_session, running_study, tmp_path, monkeypatch
):
    """A live publisher between directory moves owns its publish backup."""
    monkeypatch.setattr(
        worker_module.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path)
    )
    snapshot = {"framework": "mem0", "history_answer_ids": ["a1"]}
    store = _add_store(
        db_session,
        running_study.id,
        model=LUNA,
        status="completed",
        snapshot_json=snapshot,
        snapshot_sha256=worker_module.memory_hash(snapshot),
    )
    _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="memory_write",
        status=CallStatus.running,
        memory_store_id=store.id,
    )
    condition = tmp_path / running_study.id / "memory" / LUNA / "q2" / "mem0_full"
    committed = condition / "order_1"
    committed.mkdir(parents=True)
    store.snapshot_path = str(committed / "snapshot.json")
    db_session.commit()
    (committed / "snapshot.json").write_text(
        worker_module._json(snapshot), encoding="utf-8"
    )
    backup = condition / f"publish-{store.id}-midpublish"
    backup.mkdir()
    (backup / "snapshot.json").write_text(
        worker_module._json(snapshot), encoding="utf-8"
    )

    worker_module._cleanup_stale_attempt_dirs(running_study.id)

    assert backup.is_dir()
    assert (backup / "snapshot.json").read_text(
        encoding="utf-8"
    ) == worker_module._json(snapshot)
    assert (committed / "snapshot.json").exists()


def _worker():
    return MemoryStudyWorker(runner_factory=lambda: _UnusedRunner())


class _UnusedRunner:
    """The tests patch the call body, so the runner must never be invoked."""

    def run(self, **kwargs):
        raise AssertionError("runner should not be reached in this test")


def _flaky_score(*, failures: int, message: str = "codex exec exited with 1"):
    """Patch target for _execute_score: fail ``failures`` times, then succeed.

    The default message is a runner-level crash, which classifies as
    ``runner_transient`` (requeued with backoff).  Pass a permanent message to
    exercise the terminal-failure path.
    """
    state = {"runs": 0}

    def flaky(self, db, study, call, runner, model):
        state["runs"] += 1
        if state["runs"] <= failures:
            raise worker_module.RunnerExecutionError(message)
        return None

    return flaky


PERMANENT_RUNNER_ERROR = "codex exec crashed without a diagnostic"


def test_failure_stops_queue_for_manual_retry(db_session, running_study, monkeypatch):
    """A failed call is terminal and later calls remain untouched."""
    call = _add_call(db_session, running_study.id, model=LUNA)
    later = _add_call(db_session, running_study.id, model=TERRA, answer_id="a2")
    monkeypatch.setattr(
        MemoryStudyWorker,
        "_execute_score",
        _flaky_score(failures=99, message=PERMANENT_RUNNER_ERROR),
    )

    ok = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )
    assert ok is worker_module._CallOutcome.FAILED
    db_session.refresh(call)
    db_session.refresh(later)
    assert call.status == CallStatus.failed
    assert call.failure_code == "runner_execution_error"
    attempts = (
        db_session.query(worker_module.MSCallAttempt)
        .filter(worker_module.MSCallAttempt.call_id == call.id)
        .all()
    )
    assert [a.attempt_number for a in attempts] == [1]
    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.attention_required
    assert later.status == CallStatus.pending

    # The study is paused at the failed head, so no later call can be claimed.
    assert (
        claim_next_call(db_session, running_study.id, worker_id="w1", slot_id="slot-0")
        is None
    )


def test_non_transient_runner_failure_stops_on_first_attempt(
    db_session, running_study, monkeypatch
):
    """A non-transient runner failure stops on the first attempt."""
    call = _add_call(db_session, running_study.id, model=LUNA)
    monkeypatch.setattr(
        MemoryStudyWorker,
        "_execute_score",
        _flaky_score(failures=1, message=PERMANENT_RUNNER_ERROR),
    )

    ok = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )
    assert ok is worker_module._CallOutcome.FAILED
    db_session.refresh(call)
    assert call.status == CallStatus.failed
    assert call.attempt_count == 1
    assert call.failure_code == "runner_execution_error"


def test_transient_failure_is_retried_twice_then_succeeds(
    db_session, running_study, monkeypatch
):
    """429-like failures use the bounded three-attempt policy."""
    call = _add_call(db_session, running_study.id, model=LUNA)
    state = {"runs": 0}

    def flaky(self, db, study, call, runner, model):
        state["runs"] += 1
        if state["runs"] < 3:
            raise worker_module.RunnerExecutionError("HTTP 429 rate_limit")

    monkeypatch.setattr(MemoryStudyWorker, "_execute_score", flaky)
    monkeypatch.setattr(worker_module.time, "sleep", lambda _seconds: None)

    ok = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-luna",
        runner=_UnusedRunner(),
    )

    assert ok is worker_module._CallOutcome.SUCCEEDED
    db_session.refresh(call)
    assert call.status == CallStatus.succeeded
    assert call.attempt_count == 3
    attempts = (
        db_session.query(worker_module.MSCallAttempt)
        .filter(worker_module.MSCallAttempt.call_id == call.id)
        .order_by(worker_module.MSCallAttempt.attempt_number)
        .all()
    )
    # The patched score body does not create the production success-attempt
    # row; both failed executions still remain fully auditable.
    assert [attempt.status for attempt in attempts] == ["failed", "failed"]
    assert [attempt.error_type for attempt in attempts] == [
        "runner_transient",
        "runner_transient",
    ]


def test_permanent_failure_stops_at_first_attempt(
    db_session, running_study, monkeypatch
):
    """A validation error is terminal on the first attempt."""
    call = _add_call(db_session, running_study.id, model=LUNA)

    def invalid(self, db, study, call, runner, model):
        raise ValueError("model score exceeds the training-derived maximum")

    monkeypatch.setattr(MemoryStudyWorker, "_execute_score", invalid)
    ok = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )
    assert ok is worker_module._CallOutcome.FAILED
    db_session.refresh(call)
    assert call.status == CallStatus.failed
    attempts = (
        db_session.query(worker_module.MSCallAttempt)
        .filter(worker_module.MSCallAttempt.call_id == call.id)
        .all()
    )
    assert len(attempts) == 1  # no retry for a validation error


def test_exhausted_transient_failure_is_deferred_with_backoff(
    db_session, running_study, monkeypatch
):
    """Retry-budget exhaustion defers the call instead of failing the shard."""
    call = _add_call(db_session, running_study.id, model=LUNA)

    def flaky(self, db, study, call, runner, model):
        raise worker_module.RunnerExecutionError("HTTP 429 rate_limit")

    monkeypatch.setattr(MemoryStudyWorker, "_execute_score", flaky)
    monkeypatch.setattr(worker_module.time, "sleep", lambda _seconds: None)

    outcome = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-luna",
        runner=_UnusedRunner(),
    )
    assert outcome is worker_module._CallOutcome.DEFERRED
    db_session.expire_all()
    db_session.refresh(call)
    assert call.status == CallStatus.pending
    assert call.retry_after is not None
    assert call.retry_after > utc_now_naive()
    # 快速重试预算（3 次 attempt）完整入账，之后进入指数退避。
    assert call.attempt_count == 3
    attempts = (
        db_session.query(worker_module.MSCallAttempt)
        .filter(worker_module.MSCallAttempt.call_id == call.id)
        .order_by(worker_module.MSCallAttempt.attempt_number)
        .all()
    )
    assert all(attempt.status == "failed" for attempt in attempts)
    db_session.refresh(running_study)
    # 分片不再因瞬态失败被暂停，研究保持 running。
    assert running_study.status == StudyStatus.running


def test_deferred_call_is_not_claimable_until_retry_after(db_session, running_study):
    """A deferred call stays unclaimable while its backoff has not elapsed."""
    call = _add_call(db_session, running_study.id, model=LUNA)
    call.status = CallStatus.pending
    call.retry_after = utc_now_naive() + timedelta(minutes=5)
    db_session.commit()

    assert (
        claim_next_call(db_session, running_study.id, worker_id="w1", slot_id="slot-0")
        is None
    )

    call.retry_after = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()
    claimed = claim_next_call(
        db_session, running_study.id, worker_id="w1", slot_id="slot-0"
    )
    assert claimed is not None and claimed.id == call.id
    assert claimed.retry_after is None


def test_no_feedback_wave_gate_is_per_shard(db_session, running_study):
    """Another shard's pending full calls no longer block this shard's wave."""
    shard_a = worker_module.MSQuestionRun(
        study_id=running_study.id, question_id="q1", status="running"
    )
    shard_b = worker_module.MSQuestionRun(
        study_id=running_study.id, question_id="q2", status="running"
    )
    db_session.add_all([shard_a, shard_b])
    full_blocker = _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="memory_write",
        condition="retrieval_full",
        question_id="q2",
    )
    wave_call = _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="score",
        condition="retrieval_no_feedback",
        question_id="q1",
    )
    db_session.commit()

    claimed = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w1",
        slot_id="slot-luna-retrieval-full",
        model=LUNA,
        condition="retrieval_no_feedback",
    )
    # q1 的 full 波次已排空：q2 仍挂着 full 调用也不得阻塞 q1 的 no_feedback。
    assert claimed is not None and claimed.id == wave_call.id
    assert full_blocker.status == CallStatus.pending


def test_runner_unavailable_is_retryable(db_session, running_study):
    call = _add_call(db_session, running_study.id, model=LUNA)
    outcome = _worker()._execute_call(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=None,
    )
    assert outcome == worker_module._CallOutcome.FAILED


def test_finalize_marks_attention_and_completed(db_session, running_study):
    _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        status="failed",
        failure_code="runner_execution_error",
    )
    _add_call(db_session, running_study.id, model=TERRA, status="succeeded")
    _worker()._finalize_study(running_study.id)
    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.attention_required
    assert "runner_execution_error" in (running_study.error_summary or "")

    # Everything terminal and successful -> completed.
    running_study.status = StudyStatus.running
    db_session.query(worker_module.MSCall).filter(
        worker_module.MSCall.study_id == running_study.id
    ).update({worker_module.MSCall.status: CallStatus.succeeded})
    db_session.commit()
    _worker()._finalize_study(running_study.id)
    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.completed


def test_finalize_ignores_still_running_queue(db_session, running_study):
    _add_call(db_session, running_study.id, model=LUNA, status="pending")
    _worker()._finalize_study(running_study.id)
    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.running


def _add_store(
    db_session,
    study_id,
    *,
    model,
    status="pending",
    question_id="q2",
    framework="mem0",
    condition="mem0_full",
    **kw,
):
    store = worker_module.MSMemoryStore(
        study_id=study_id,
        model=model,
        question_id=question_id,
        framework=framework,
        condition=condition,
        feedback_mode="full",
        order_variant="order_1",
        status=status,
        **kw,
    )
    db_session.add(store)
    db_session.commit()
    return store


def _failing_memory_write(self, db, study, call, runner, model):
    """Patch target for _execute_memory_write: never ingest successfully."""
    raise worker_module.RunnerExecutionError("framework ingest rejected the payload")


def test_store_failure_blocks_remaining_writes(db_session, running_study, monkeypatch):
    """A failed write pauses the study and leaves later writes pending."""
    monkeypatch.setattr(
        MemoryStudyWorker, "_execute_memory_write", _failing_memory_write
    )
    store = _add_store(db_session, running_study.id, model=LUNA)
    calls = [
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            kind="memory_write",
            memory_store_id=store.id,
            answer_id=f"a{i}",
        )
        for i in range(3)
    ]

    _worker()._execute_call_strict(
        running_study.id,
        calls[0].id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )

    db_session.refresh(calls[0])
    db_session.refresh(store)
    assert calls[0].status == CallStatus.failed
    assert store.status == "failed"  # derived from the ledger, not forced
    for call in calls[1:]:
        db_session.refresh(call)
        assert call.status == CallStatus.pending
        assert call.failure_code is None

    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.attention_required
    assert (
        claim_next_call(db_session, running_study.id, worker_id="w1", slot_id="slot-0")
        is None
    )


def test_finalize_still_waits_for_inflight_call(db_session, running_study):
    """The early return stays: a genuinely in-flight call must still block."""
    _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        status="running",
        lease_until=utc_now_naive() + timedelta(seconds=60),
    )
    _worker()._finalize_study(running_study.id)
    db_session.refresh(running_study)
    assert running_study.status == StudyStatus.running


def test_all_memory_resolved_treats_failed_as_resolved(db_session, running_study):
    """A failed store ends the training phase; a pending one does not."""
    assert worker_module._all_memory_resolved(db_session, running_study.id) is True

    failed_store = _add_store(db_session, running_study.id, model=LUNA, status="failed")
    assert worker_module._all_memory_resolved(db_session, running_study.id) is True

    failed_store.status = "pending"
    db_session.commit()
    assert worker_module._all_memory_resolved(db_session, running_study.id) is False


def test_store_progress_is_derived_from_the_ledger(db_session, running_study):
    """A replayed store must not be reported complete on a partial replay."""
    store = _add_store(db_session, running_study.id, model=LUNA)
    calls = [
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            kind="memory_write",
            status="succeeded",
            memory_store_id=store.id,
            answer_id=f"a{i}",
        )
        for i in range(3)
    ]

    recompute_store_progress(db_session, store)
    assert store.committed_count == 3
    assert store.status == "completed"

    # A requeue (manual retry) reopens the stream: the count must fall with it
    # instead of staying at 3 and instantly re-satisfying the completion check.
    for call in calls:
        call.status = CallStatus.pending
    db_session.flush()
    recompute_store_progress(db_session, store)
    assert store.committed_count == 0
    assert store.status == "pending"

    calls[0].status = CallStatus.succeeded
    calls[1].status = CallStatus.failed
    db_session.flush()
    recompute_store_progress(db_session, store)
    assert store.committed_count == 1
    assert store.status == "failed"


def test_store_progress_keeps_retrieval_stores_completed(db_session, running_study):
    """Retrieval stores own no writes; they must not be flipped to pending."""
    store = _add_store(
        db_session,
        running_study.id,
        model=LUNA,
        status="completed",
        framework="retrieval",
        condition="retrieval_full",
        question_id="q3",
    )
    recompute_store_progress(db_session, store)
    assert store.status == "completed"
    assert store.committed_count == 0


def test_successful_write_completes_store_without_autoflush(
    db_session, running_study, monkeypatch
):
    """Pin the production session settings, which disable autoflush.

    The completion verdict is a ledger count, so the call's own ``succeeded``
    status must be flushed before that count runs -- otherwise the store never
    reaches ``completed`` and the global scoring barrier stays shut forever.
    The default test session autoflushes, which would hide exactly this bug, so
    this case builds a session that matches production instead.
    """
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr(worker_module, "SessionLocal", factory)

    store = _add_store(db_session, running_study.id, model=LUNA)
    call = _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="memory_write",
        memory_store_id=store.id,
    )
    monkeypatch.setattr(
        MemoryStudyWorker, "_execute_memory_write", lambda *a, **kw: None
    )

    outcome = _worker()._execute_call(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )
    assert outcome == worker_module._CallOutcome.SUCCEEDED
    with factory() as check:
        assert check.get(worker_module.MSMemoryStore, store.id).status == "completed"


# --- failure evidence -------------------------------------------------------
#
# A failed write is the moment the run most needs a record, and that record used
# to be exactly what the failure path destroyed.


def test_failed_write_keeps_the_framework_invocation_ledger(
    db_session, running_study, monkeypatch
):
    """A transport failure must not erase the rows that explain it.

    ``FrameworkInvocationRecorder`` flushes one row per internal request onto the
    caller's session, and ``_execute_call`` used to roll that session back
    unconditionally.  The result was an empty ``ms_framework_invocations`` table
    for a run in which every write had failed, which also silenced
    ``audit_project``'s ``no_failed_framework_invocations`` check and reported
    zero failed invocations in the progress counters.
    """
    store = _add_store(db_session, running_study.id, model=LUNA)
    call = _add_call(
        db_session,
        running_study.id,
        model=LUNA,
        kind="memory_write",
        memory_store_id=store.id,
    )

    class RejectingRunner:
        def run(self, **kwargs):
            raise worker_module.RunnerExecutionError(
                "codex exec rejected the request: invalid_json_schema"
            )

    def failing_write(self, db, study, call, runner, model):
        # Drives the real recorder, so the ledger row is written by production
        # code rather than by the test.
        recorder = worker_module.FrameworkInvocationRecorder(
            db=db, call=call, framework="mem0"
        )
        recorder.invoke(
            runner=RejectingRunner(),
            messages=({"role": "user", "content": "case"},),
            runtime={"model": LUNA, "reasoning_effort": "medium"},
            schema_json={
                "type": "object",
                "properties": {"memory": {"type": "array"}},
                "required": ["memory"],
                "additionalProperties": False,
            },
            phase="mem0.generate_response",
        )

    monkeypatch.setattr(MemoryStudyWorker, "_execute_memory_write", failing_write)

    ok = _worker()._execute_call_strict(
        running_study.id,
        call.id,
        model=LUNA,
        worker_id="w1",
        slot_id="slot-0",
        runner=_UnusedRunner(),
    )

    assert ok is worker_module._CallOutcome.FAILED
    db_session.expire_all()
    rows = list(
        db_session.query(worker_module.MSFrameworkInvocation).filter(
            worker_module.MSFrameworkInvocation.call_id == call.id
        )
    )
    assert len(rows) == 1, "the invocation ledger was rolled back"
    assert rows[0].status == "failed"
    assert rows[0].phase == "mem0.generate_response"
    assert "invalid_json_schema" in rows[0].error_message

    failed_call = db_session.get(worker_module.MSCall, call.id)
    assert failed_call.status == CallStatus.failed
    # The cause reaches the call ledger too, so the UI and the report can show it.
    assert "invalid_json_schema" in failed_call.failure_summary
    assert db_session.get(worker_module.MSMemoryStore, store.id).status == "failed"


def _add_shard(db_session, study_id, question_id, *, status="pending"):
    from app.models.memory_study import MSQuestionRun

    shard = MSQuestionRun(study_id=study_id, question_id=question_id, status=status)
    db_session.add(shard)
    db_session.commit()
    return shard


def test_claim_runs_only_launched_shard_even_deep_in_id_order(
    db_session, running_study
):
    """Regression: a late-launched shard must claim despite its call ids.

    Freeze materialises calls interleaved by question, so an early-launched
    study can put another question's calls first.  Starting only ``q_late``
    (whose calls sit far deeper in the id order than the 256-row claim window)
    must serve that shard and never touch the not-launched one.
    """

    # Not-launched shards own every pending call inside the head window.
    for index in range(300):
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            question_id="q_first",
            answer_id=f"a{index}",
        )
    # The launched shard's calls sit beyond any head window.
    late_calls = [
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            question_id="q_late",
            answer_id=f"b{index}",
        )
        for index in range(3)
    ]
    _add_shard(db_session, running_study.id, "q_first", status="pending")
    _add_shard(db_session, running_study.id, "q_late", status="running")

    claimed = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w1",
        slot_id="slot-0",
    )
    assert claimed is not None
    assert claimed.id == late_calls[0].id
    assert claimed.question_id == "q_late"
    db_session.refresh(late_calls[1])
    assert late_calls[1].status == CallStatus.pending
    # The not-launched shard's calls stay untouched.
    untouched = db_session.query(worker_module.MSCall).filter(
        worker_module.MSCall.question_id == "q_first",
        worker_module.MSCall.status != CallStatus.pending,
    )
    assert untouched.count() == 0


def test_claim_returns_none_when_no_shard_is_running(db_session, running_study):
    _add_call(db_session, running_study.id, model=LUNA)
    _add_shard(db_session, running_study.id, "q1", status="paused")

    assert (
        claim_next_call(db_session, running_study.id, worker_id="w1", slot_id="slot-0")
        is None
    )


def test_claim_without_shard_rows_keeps_legacy_behavior(db_session, running_study):
    """Studies frozen before shards keep the fully claimable queue."""
    first = _add_call(db_session, running_study.id, model=LUNA)
    second = _add_call(db_session, running_study.id, model=LUNA, answer_id="a2")

    claimed = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w1",
        slot_id="slot-0",
    )
    assert claimed is not None
    assert claimed.id == first.id
    db_session.refresh(second)
    assert second.status == CallStatus.pending


def test_pause_question_stops_claims_for_that_shard_only(db_session, running_study):

    paused_calls = [
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            question_id="q_paused",
            answer_id=f"a{index}",
        )
        for index in range(3)
    ]
    running_calls = [
        _add_call(
            db_session,
            running_study.id,
            model=LUNA,
            question_id="q_running",
            answer_id=f"b{index}",
        )
        for index in range(3)
    ]
    _add_shard(db_session, running_study.id, "q_paused", status="paused")
    _add_shard(db_session, running_study.id, "q_running", status="running")

    claimed = claim_next_call(
        db_session,
        running_study.id,
        worker_id="w1",
        slot_id="slot-0",
    )
    assert claimed is not None
    assert claimed.question_id == "q_running"
    assert claimed.id in {call.id for call in running_calls}
    db_session.refresh(paused_calls[0])
    assert paused_calls[0].status == CallStatus.pending


def test_resume_clears_stale_error_summaries(db_session, monkeypatch):
    """Resume must drop the outdated shard/study error banners."""
    from app.models.memory_study import MSQuestionRun
    from app.services import memory_study as service_module
    from app.services.memory_study import MemoryStudyService

    fingerprint = {
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

    class FakeResumeRunner:
        def runtime_fingerprint(self):
            return dict(fingerprint)

    monkeypatch.setattr(service_module, "CodexExecRunner", FakeResumeRunner)

    config = {"models": [{"model": LUNA}, {"model": TERRA}]}
    runtime_snapshot = {
        model: {"model": model, "reasoning_effort": "low", "speed_mode": "standard"}
        for model in (LUNA, TERRA)
    }
    frozen_runtimes = {
        model: dict(runtime_snapshot[model]) | fingerprint for model in (LUNA, TERRA)
    }
    frozen_config = dict(config)
    frozen_config["runtime_snapshot"] = frozen_runtimes
    preflight = {
        "status": "passed",
        "config_sha256": service_module._sha(frozen_config),
        "models": {
            model: {
                "status": "passed",
                "model": model,
                "runtime": {
                    "model": model,
                    "reasoning_effort": "low",
                    "speed_mode": "standard",
                },
                "fingerprint": dict(fingerprint),
            }
            for model in (LUNA, TERRA)
        },
    }

    study = MSStudy(
        id="study-resume",
        protocol_id="p",
        name="resume",
        kind="formal",
        status=StudyStatus.paused,
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json=frozen_config,
        expected_json={},
        progress_json={},
        preflight_json=preflight,
        integrity_json={},
        error_summary="题目分片尚未全部启动或完成",
    )
    db_session.add(study)
    shard = MSQuestionRun(
        study_id=study.id,
        question_id="q1",
        status="paused",
        error_summary="call 1 failed; manual retry required",
    )
    db_session.add(shard)
    db_session.commit()

    MemoryStudyService(db_session).resume(study.id)
    db_session.refresh(study)
    db_session.refresh(shard)
    assert study.status == StudyStatus.running
    assert study.error_summary is None
    assert shard.status == "running"
    assert shard.error_summary is None


def test_memory_projection_failure_names_the_framework_and_the_memory():
    """A projection failure must say which memory broke and why.

    Call #146263 only recorded "retrieval case is not valid scoring JSON",
    which is impossible to act on: it does not say whether an adapter is
    missing, which framework is at fault, or what was actually stored.
    """
    prose = (
        "User answered a TCP congestion control question by naming Slow Start "
        "and received partial credit (0.75) for the answer."
    )
    item = RetrievedCase(
        answer_id="10.2_TC.54",
        score=0.9,
        similarity=0.9,
        payload={"id": "mem-1", "memory": prose, "score": 0.9},
    )
    with pytest.raises(MemoryEvidenceError) as excinfo:
        _minimal_memory_case(item, framework="mem0")
    message = str(excinfo.value)
    assert "mem0" in message
    assert "10.2_TC.54" in message
    assert "User answered a TCP congestion control question" in message
    assert worker_module._failure_code(excinfo.value) == "memory_evidence_invalid"
    # A shape problem is deterministic, so it must stop instead of backing off.
    assert not worker_module._is_retryable_code("memory_evidence_invalid")


def test_memory_projection_failure_outranks_its_own_quoted_prose():
    """The quoted memory must never be read as an auth or transport error.

    The excerpt is untrusted framework output; a student answer containing
    "401" or "timed out" must not turn a data problem into a fake credential
    or transient failure.
    """
    item = RetrievedCase(
        answer_id="q.1",
        score=0.9,
        similarity=0.9,
        payload={"memory": "the candidate wrote 401 and the process timed out"},
    )
    with pytest.raises(MemoryEvidenceError) as excinfo:
        _minimal_memory_case(item, framework="amem")
    assert worker_module._failure_code(excinfo.value) == "memory_evidence_invalid"


def test_memory_projection_requires_the_scoring_evidence():
    """A canonical JSON case without a teacher score is still unusable."""
    item = RetrievedCase(
        answer_id="q.2",
        score=0.9,
        similarity=0.9,
        payload={"memory": json.dumps({"question": "q", "answer": "a"})},
    )
    with pytest.raises(MemoryEvidenceError) as excinfo:
        _minimal_memory_case(item, framework="retrieval")
    assert "teacher score" in str(excinfo.value)


def test_failure_code_nonzero_cli_exit_is_transient_despite_prompt_words():
    """A nonzero codex exit is a runner crash, not a schema violation.

    The diagnostic envelope embeds the prompt ("...must satisfy the supplied
    JSON Schema..."), which used to trip the generic "schema" keyword scan and
    misclassify a transient crash as a non-retryable schema_error, pausing the
    study for manual retry.
    """
    envelope = (
        "mem0 official ingest failed: LLM extraction failed: codex exec "
        "exited with 1; stderr: Reading additional input from stdin... "
        "OpenAI Codex v0.154.0 user You are an isolated measurement runner. "
        "Your final response must satisfy the supplied JSON Schema. "
        "SYSTEM INSTRUCTION: extract memories"
    )
    assert worker_module._failure_code(RuntimeError(envelope)) == "runner_transient"


def test_failure_code_nonzero_exit_with_auth_marker_still_auth_error():
    """Auth detection outranks the nonzero-exit heuristic."""
    envelope = (
        "codex exec exited with 1; stderr: unexpected status 401 "
        "unauthorized: check your credentials"
    )
    assert worker_module._failure_code(RuntimeError(envelope)) == "authentication_error"


def test_heartbeat_renews_lease_while_slot_future_is_running(monkeypatch):
    """Regression: the liveness check must read slot futures, not threads.

    The old wiring stored the ``pool.submit`` Future in ``slot_threads`` and
    called ``.is_alive()`` on it.  Future has no such method, so every
    heartbeat tick raised AttributeError, the loop's broad except swallowed
    it, the runtime lease was never renewed, and the UI flipped to
    "worker is not connected" about 10 minutes into every study.
    """
    import time
    from concurrent.futures import Future

    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    touches: list[str] = []
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_runtime",
        lambda worker_id: touches.append(worker_id),
    )
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_running_call_leases",
        lambda worker_id: None,
    )
    slot = Future()
    heartbeat = worker_module._HeartbeatThread(
        "w-hb", liveness_check=lambda: not slot.done()
    )
    heartbeat.start()
    try:
        time.sleep(0.4)
        assert touches, "lease must be renewed while a slot future is running"
        assert all(worker_id == "w-hb" for worker_id in touches)
        slot.set_result(None)
        heartbeat._thread.join(timeout=2)
        assert not heartbeat._thread.is_alive()
        after_stop = len(touches)
        time.sleep(0.15)
        assert len(touches) == after_stop, "no renewal once every slot has exited"
    finally:
        heartbeat.stop()


def test_heartbeat_survives_transient_liveness_failure(monkeypatch):
    """One failed liveness check must not kill the heartbeat thread.

    The main loop respawns dead slot futures within 30s; a single all-done
    snapshot is a transient window.  Exiting on the first failure left the
    respawned slots working with nobody renewing the lease, and the UI
    flipped to "not connected" 10 minutes later while the study still ran.
    """
    import time

    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_runtime",
        lambda worker_id: None,
    )
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_running_call_leases",
        lambda worker_id: None,
    )
    checks = {"alive": True}
    heartbeat = worker_module._HeartbeatThread(
        "w-hb2", liveness_check=lambda: checks["alive"]
    )
    heartbeat.start()
    try:
        time.sleep(0.12)
        assert heartbeat._thread.is_alive()
        # One transient failure: the thread must keep running.
        checks["alive"] = False
        time.sleep(0.12)
        assert (
            heartbeat._thread.is_alive()
        ), "a single liveness failure must not exit the heartbeat loop"
        # Recovery resets the consecutive-failure budget.
        checks["alive"] = True
        time.sleep(0.12)
        assert heartbeat._thread.is_alive()
        assert heartbeat._liveness_failures == 0
    finally:
        heartbeat.stop()


def test_heartbeat_exits_after_consecutive_liveness_failures(monkeypatch):
    """Persistent all-slots-dead must still release the lease eventually."""
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_runtime",
        lambda worker_id: None,
    )
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_running_call_leases",
        lambda worker_id: None,
    )
    heartbeat = worker_module._HeartbeatThread("w-hb3", liveness_check=lambda: False)
    heartbeat.start()
    try:
        heartbeat._thread.join(timeout=2)
        assert (
            not heartbeat._thread.is_alive()
        ), "heartbeat must exit after LIVENESS_FAILURE_LIMIT consecutive failures"
    finally:
        heartbeat.stop()


def test_heartbeat_logs_wall_clock_sleep_gap(monkeypatch, caplog):
    """A wall-clock gap between ticks is surfaced as a sleep warning."""
    import time as time_module

    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_runtime",
        lambda worker_id: None,
    )
    monkeypatch.setattr(
        worker_module.MemoryStudyWorker,
        "_touch_running_call_leases",
        lambda worker_id: None,
    )
    fake_now = {"value": 1_000_000.0}
    monkeypatch.setattr(time_module, "time", lambda: fake_now["value"])
    heartbeat = worker_module._HeartbeatThread("w-hb4")
    try:
        with caplog.at_level("WARNING", logger="ai_marking.memory_study.worker"):
            heartbeat.start()
            time_module.sleep(0.12)
            # Simulate the wall clock jumping forward past the sleep
            # threshold (macOS suspend pauses the monotonic clock but the
            # wall clock keeps running).
            fake_now["value"] += worker_module.HEARTBEAT_SLEEP_GAP_SECONDS + 60
            time_module.sleep(0.12)
        assert any(
            "system likely slept" in record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        ), "a >120s wall-clock gap must be logged as a likely suspend event"
    finally:
        heartbeat.stop()


def test_worker_alive_variants(monkeypatch):
    """_worker_alive: dead pid → False; live/foreign/unknown → True."""
    from app.experiment.memory_study import worker as wm

    monkeypatch.setattr(wm.socket, "gethostname", lambda: "host-a")

    def kill_raises(exc):
        def _kill(pid, sig):
            raise exc

        return _kill

    # Dead process on this host.
    monkeypatch.setattr(wm.os, "kill", kill_raises(ProcessLookupError))
    assert wm._worker_alive(4242, "host-a") is False
    # Live process owned by another user.
    monkeypatch.setattr(wm.os, "kill", kill_raises(PermissionError))
    assert wm._worker_alive(4242, "host-a") is True
    # Live process.
    monkeypatch.setattr(wm.os, "kill", lambda pid, sig: None)
    assert wm._worker_alive(4242, "host-a") is True
    # Unknown identity / foreign host: conservative.
    assert wm._worker_alive(None, "host-a") is True
    assert wm._worker_alive(4242, "host-b") is True
    assert wm._worker_alive(None, None) is True


def _seed_runtime(db_session, **kw):
    runtime = worker_module.MSSchedulerRuntime(
        id=1,
        owner_id=kw.get("owner_id", "w-old"),
        status=kw.get("status", "online"),
        max_subprocesses=2,
        active_subprocesses=0,
        slots_json={},
        heartbeat_at=utc_now_naive(),
        lease_until=kw.get("lease_until", utc_now_naive() + timedelta(seconds=600)),
        worker_pid=kw.get("worker_pid"),
        worker_host=kw.get("worker_host"),
    )
    db_session.add(runtime)
    db_session.commit()
    return runtime


def test_acquire_runtime_force_recovers_dead_lease(db_session, monkeypatch):
    """A live lease whose holder process is dead must be taken over at once.

    Waiting out the full 600s expiry left the UI on "not connected" for up
    to 10 minutes after every crash while the supervision loop idled.
    """
    _seed_runtime(
        db_session,
        owner_id="w-old",
        worker_pid=4242,
        worker_host="host-a",
    )
    monkeypatch.setattr(worker_module, "_worker_alive", lambda pid, host: False)
    monkeypatch.setattr(worker_module.os, "getpid", lambda: 9999)
    monkeypatch.setattr(worker_module.socket, "gethostname", lambda: "host-a")

    MemoryStudyWorker()._acquire_runtime("w-new", "study-1", slot_models={})

    db_session.expire_all()
    row = db_session.get(worker_module.MSSchedulerRuntime, 1)
    assert row.owner_id == "w-new"
    assert row.worker_pid == 9999
    assert row.worker_host == "host-a"


def test_acquire_runtime_still_raises_for_live_owner(db_session, monkeypatch):
    _seed_runtime(
        db_session,
        owner_id="w-old",
        worker_pid=4242,
        worker_host="host-a",
    )
    monkeypatch.setattr(worker_module, "_worker_alive", lambda pid, host: True)
    with pytest.raises(RuntimeError, match="another memory-study worker"):
        MemoryStudyWorker()._acquire_runtime("w-new", "study-1", slot_models={})


def test_studies_to_adopt_takes_over_dead_lease(
    db_session, db_session_factory, running_study, monkeypatch
):
    """run_memory_study adoption must not wait out a dead worker's lease."""
    import scripts.run_memory_study as runner

    _seed_runtime(
        db_session,
        owner_id="w-old",
        worker_pid=4242,
        worker_host="host-a",
    )
    monkeypatch.setattr(runner, "SessionLocal", db_session_factory)
    monkeypatch.setattr(runner, "_worker_alive", lambda pid, host: False)
    assert runner._studies_to_adopt() == [running_study.id]

    monkeypatch.setattr(runner, "_worker_alive", lambda pid, host: True)
    assert runner._studies_to_adopt() == []


def test_question_score_ceiling_prefers_registered_ceiling(tmp_path):
    """The acceptance gate and prompt read the registered question ceiling,
    falling back to the legacy training-observed max for older manifests."""
    from types import SimpleNamespace

    from app.experiment.memory_study.worker import _question_score_ceiling

    current = SimpleNamespace(
        data_manifest_json={
            "score_ceilings": {"q1": 3.5},
            "max_scores_from_training": {"q1": 3.0},
        }
    )
    legacy = SimpleNamespace(
        data_manifest_json={"max_scores_from_training": {"q1": 3.0}}
    )
    assert _question_score_ceiling(current, "q1") == 3.5
    assert _question_score_ceiling(legacy, "q1") == 3.0
