"""Four-slot, resumable worker for the SAF memory study.

Each slot owns one model/condition stream.  Calls inside a stream remain
strictly ordered while question shards share the fixed global slot budget.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import socket
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.experiment.common.codex_runner import (
    CodexExecRunner,
    RunnerDriftError,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.experiment.common.tokenization import token_count
from app.experiment.memory_study.codex_bridge import FrameworkInvocationRecorder
from app.experiment.memory_study.memory import (
    MemoryEvidenceError,
    MemoryFrameworkError,
    OpenAICompatibleEmbeddingProvider,
    _redact_text,
    _sha256_path,
    create_adapter,
    memory_hash,
    payload_token_counts,
    verify_official_memory_dependencies,
)
from app.experiment.memory_study.protocol import (
    DEFAULT_PROTOCOL_ID,
    INITIAL_TIMEOUT_SECONDS,
    LEGACY_SCORING_SYSTEM_PROMPT,
    MAX_CODEX_SUBPROCESSES,
    MODEL_IDS,
    PROTOCOL_ID,
    SUPPORTED_PROTOCOL_IDS,
    V3_R2_PROTOCOL_ID,
    V3_R2_SCORING_SYSTEM_PROMPT,
    CallKind,
    CallStatus,
    ScoreOutput,
    StudyStatus,
    memory_profile_for_protocol,
)
from app.models.memory_study import (
    MSCall,
    MSCallAttempt,
    MSFrameworkInvocation,
    MSMemoryStore,
    MSQuestionRun,
    MSSchedulerRuntime,
    MSStudy,
)

logger = logging.getLogger("ai_marking.memory_study.worker")

LEASE_SECONDS = 600
HEARTBEAT_SECONDS = 10
# 心跳的存活检查连续失败达到该次数才停止续租：单次失败（例如主循环对账
# 间隔内所有槽 future 恰好同时 done）只是瞬时窗口，直接退出会让重建后的
# 槽继续干活但租约无人续期，600 秒后 UI 必报 "not connected"。
LIVENESS_FAILURE_LIMIT = 3
# 墙钟心跳间隔超过该阈值视为系统休眠/冻结（正常 tick 为 10s；必须用墙钟，
# macOS 休眠期间单调时钟暂停而墙钟继续走）。
HEARTBEAT_SLEEP_GAP_SECONDS = 120


def _worker_alive(pid: int | None, host: str | None) -> bool:
    """Return whether the lease-holding worker process is still alive.

    A crashed worker leaves its lease behind with ``status="online"``; without
    this check the replacement process must wait out the full 600s lease
    before adopting the study.  Unknown identities (no pid, or a foreign
    host) are conservatively treated as alive so the time-based expiry stays
    the safety net.
    """
    if pid is None:
        return True
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user: treat as alive.
        return True
    except OSError:
        # Unexpected platform error: be conservative and let time decide.
        return True
    return True


# Public protocol wording is expressed in retries, while the execution loop
# counts the initial attempt as well.  Keeping both names makes the boundary
# unambiguous: two automatic retries means at most three attempts per logical
# call; a later manual retry starts a fresh automatic-retry budget.
MAX_AUTOMATIC_RETRIES = 2
MAX_AUTOMATIC_ATTEMPTS = MAX_AUTOMATIC_RETRIES + 1
RETRY_BACKOFF_SECONDS = (2.0, 4.0)
RETRY_JITTER_SECONDS = 0.25
# 基础设施类瞬态失败在快速重试预算耗尽后转为指数退避重新排队，而不是终态
# 失败暂停分片。退避序列 1min, 2min, 4min, ... 封顶 30min，等待期间槽可以
# 服务其他分片；只有下面集合中的永久错误才要求人工介入。
TRANSIENT_RETRY_BASE_SECONDS = 60
TRANSIENT_RETRY_MAX_SECONDS = 1800
# 这些失败码代表数据/配置级问题，退避重试不可能自愈，必须人工决策。
PENDING_NON_RETRYABLE_CODES = frozenset(
    {
        "authentication_error",
        "schema_error",
        "snapshot_validation_error",
        "dependency_error",
        "model_configuration_error",
        "runtime_drift_error",
        "runner_unavailable",
    }
)
# 研究仍在 running 且存在 pending 调用、但连续这么久没有任何调用完成时，
# 运行台把停滞原因暴露出来（正常波次等待除外）。
STALL_THRESHOLD_SECONDS = 300
SLOT_MODELS = {f"slot-{model.removeprefix('gpt-6-')}": model for model in MODEL_IDS}
BASE_SLOT_CONDITIONS = ("no_memory", "retrieval_full", "mem0_full", "amem_full")


def slot_specs(
    models: tuple[str, ...] | list[str] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> dict[str, tuple[str, str]]:
    """Return fixed execution slots from the frozen protocol configuration.

    The same four physical slots are reused for the optional no-feedback
    wave after the full-feedback wave drains; no extra subprocess slots are
    created for that wave.
    """
    configured_models = MODEL_IDS if models is None else tuple(models)
    base_conditions = BASE_SLOT_CONDITIONS
    result = {
        f"slot-{model.removeprefix('gpt-6-')}-{condition.replace('_', '-')}": (
            model,
            condition,
        )
        for model in configured_models
        for condition in base_conditions
    }
    return result


def _full_wave_conditions(protocol_id: str) -> tuple[str, ...]:
    del protocol_id
    return BASE_SLOT_CONDITIONS


def _paired_feedback_condition(condition: str) -> str:
    return condition.removesuffix("_full") + "_no_feedback"


# A store is "done training" once it either published its final snapshot or
# failed terminally.  Anything else (``pending``) means the training phase is
# still open and the scoring barrier must hold.
STORE_TERMINAL_STATUSES = frozenset({"completed", "failed"})


class _CallOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # 退避重试：失败调用已回队为 pending（带 retry_after），槽应继续服务
    # 其他分片而不是把这次失败当作分片级终态。
    DEFERRED = "deferred"


class _HeartbeatThread:
    """Keep the scheduler lease alive while long framework calls run.

    A single memory write can issue several codex subprocesses, so touching
    only between calls let the lease expire mid-flight and the UI flip to
    "not connected" while the worker was still working.
    """

    def __init__(
        self, worker_id: str, *, liveness_check: Callable[[], bool] | None = None
    ):
        self.worker_id = worker_id
        # 返回 False 表示没有任何存活的槽线程在做事；此时停止续租，让租约
        # 过期回收与外部监督循环接管，而不是替一个僵尸 worker 保鲜。
        # 单次失败可能是主循环重建槽期间的瞬时窗口，只有连续失败达到
        # LIVENESS_FAILURE_LIMIT 才真正退出，避免误杀重建后的心跳。
        self._liveness_check = liveness_check
        self._liveness_failures = 0
        self._last_tick_wall = time.time()
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(
            target=self._loop, name="memory-study-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=HEARTBEAT_SECONDS * 2)

    def _loop(self) -> None:
        while not self._stop.wait(timeout=HEARTBEAT_SECONDS):
            wall_now = time.time()
            gap = wall_now - self._last_tick_wall
            self._last_tick_wall = wall_now
            if gap > HEARTBEAT_SLEEP_GAP_SECONDS:
                # 墙钟跳跃用 time.time() 检测：macOS 休眠期间单调时钟暂停、
                # 墙钟继续走，唤醒后 gap 才能反映真实冻结时长。
                logger.warning(
                    "heartbeat gap %.0fs; system likely slept or was suspended; "
                    "codex connections may have been severed",
                    gap,
                )
            try:
                if self._liveness_check is not None and not self._liveness_check():
                    self._liveness_failures += 1
                    if self._liveness_failures >= LIVENESS_FAILURE_LIMIT:
                        logger.warning(
                            "no live slot threads remain after %d checks; "
                            "letting the lease expire",
                            self._liveness_failures,
                        )
                        return
                    logger.warning(
                        "liveness check failed (%d/%d); waiting for slot respawn",
                        self._liveness_failures,
                        LIVENESS_FAILURE_LIMIT,
                    )
                    continue
                self._liveness_failures = 0
                MemoryStudyWorker._touch_runtime(self.worker_id)
                # 长框架调用（Mem0/A-MEM 多阶段 codex 子进程）可能超过固定的
                # 调用级租约；在途调用跟着心跳一起续租，杜绝"慢调用被其他
                # 进程的 sweep 误回收"。心跳自身也是调用仍被持有的证据。
                MemoryStudyWorker._touch_running_call_leases(self.worker_id)
            except Exception as exc:
                # 续租失败必须留痕：静默吞掉曾把"存活检查抛异常"变成租约
                # 永不续期，10 分钟后 UI 必报 not connected。
                logger.warning("memory-study heartbeat tick failed: %s", exc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _request_sha256(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _call_request_sha(call: MSCall) -> str:
    """Return the actual request hash while keeping sensitive prompts out of DB."""
    request = call.request_json
    if isinstance(request, dict):
        stored = request.get("request_sha256")
        if isinstance(stored, str) and len(stored) == 64:
            return stored
    return _request_sha256(request or {"call_id": call.id})


def _atomic_snapshot(path: str, snapshot: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix="snapshot-",
        suffix=".json.tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _relocate_paths(value: Any, old_root: Path, new_root: Path) -> Any:
    """Relocate absolute stream paths embedded in a freshly written snapshot."""
    old = str(old_root.resolve()).rstrip(os.sep)
    new = str(new_root.resolve()).rstrip(os.sep)
    if isinstance(value, str):
        if value == old:
            return new
        prefix = old + os.sep
        if value.startswith(prefix):
            return new + value[len(old) :]
        return value
    if isinstance(value, dict):
        return {
            key: _relocate_paths(item, old_root, new_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_relocate_paths(item, old_root, new_root) for item in value]
    if isinstance(value, tuple):
        return [_relocate_paths(item, old_root, new_root) for item in value]
    return value


def _in_flight_store_ids(study_id: str) -> set[int] | None:
    """Ids of stores whose calls are currently leased or running.

    ``None`` means the database could not be consulted, so callers must treat
    every staging directory as potentially live instead of guessing.
    """
    try:
        with SessionLocal() as db:
            return set(
                db.scalars(
                    select(MSCall.memory_store_id).where(
                        MSCall.study_id == study_id,
                        MSCall.memory_store_id.is_not(None),
                        MSCall.status.in_([CallStatus.leased, CallStatus.running]),
                    )
                )
            )
    except Exception:
        return None


def _cleanup_stale_attempt_dirs(study_id: str) -> None:
    """Remove abandoned staging dirs without discarding a committed snapshot.

    Publication uses a sibling ``publish-<store-id>-*`` backup while the new
    attempt is moved into the committed stream.  A process can crash between
    those directory moves and the database commit.  On the next start, use the
    persisted snapshot metadata to distinguish the two safe outcomes: keep a
    filesystem state already reflected in the DB, otherwise restore the backup
    before removing it.  Unknown backups are left for the read-only audit
    command rather than guessed away.

    Staging dirs of stores with a leased/running call are never touched: a
    re-entered ``run()`` shares the process with the previous invocation's
    slot threads, and deleting their staging tree mid-framework-op surfaces
    as amem ENOENT / mem0 "readonly database" failures.  Such dirs survive
    this pass and are removed on a later pass once the call is terminal.
    """
    root = (Path(settings.MEMORY_STUDY_ARTIFACT_ROOT) / study_id / "memory").resolve()
    if not root.is_dir():
        return
    in_flight = _in_flight_store_ids(study_id)
    if in_flight is None:
        return
    stores: dict[str, MSMemoryStore] = {}
    try:
        with SessionLocal() as db:
            stores = {
                str(store.id): store
                for store in db.scalars(
                    select(MSMemoryStore).where(MSMemoryStore.study_id == study_id)
                )
            }
    except Exception:
        # Cleanup is best-effort.  In particular, never remove a publish
        # backup when its database owner cannot be resolved.
        stores = {}
    protected = [
        path
        for path in root.rglob("attempt-*")
        if path.is_dir()
        and not path.is_symlink()
        and (match := re.fullmatch(r"attempt-(\d+)-[^/]+", path.name))
        and int(match.group(1)) in in_flight
    ]
    handled_publish: set[Path] = set()
    allowed_root = (
        Path(settings.MEMORY_STUDY_ARTIFACT_ROOT).resolve() / study_id / "memory"
    ).resolve()
    for backup in sorted(
        (
            path
            for path in root.rglob("publish-*")
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        match = re.fullmatch(r"publish-(\d+)-[^/]+", backup.name)
        store = stores.get(match.group(1)) if match else None
        if store is not None and store.id in in_flight:
            # A live publisher sits between the directory moves and its DB
            # commit; restoring or deleting the backup would corrupt it.
            continue
        if store is None or not store.snapshot_path:
            continue
        committed_root = Path(store.snapshot_path).resolve().parent
        if committed_root == allowed_root or not committed_root.is_relative_to(
            allowed_root
        ):
            continue
        try:
            if _snapshot_is_valid(store):
                # The database already points at the newly published stream;
                # only the old backup remains to be discarded.
                shutil.rmtree(backup)
            else:
                # The DB still describes the previous commit (or the stream is
                # partially moved).  Restore that exact backup before deleting
                # any attempt directory.
                committed_root.mkdir(parents=True, exist_ok=True)
                for child in list(committed_root.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
                for child in list(backup.iterdir()):
                    os.replace(child, committed_root / child.name)
                shutil.rmtree(backup)
            handled_publish.add(backup)
        except OSError:
            # Leave an unresolved backup for the artifact audit/recovery path.
            continue
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_dir()
        and (
            path.name.startswith(("attempt-", "publish-"))
            # ``native-artifacts`` is a committed Mem0 snapshot directory;
            # only the uniquely-prefixed construction staging dirs are stale.
            or path.name.startswith("native-")
            and path.name != "native-artifacts"
        )
        and not (path.name.startswith("publish-") and path not in handled_publish)
    ]
    if protected:
        candidates = [
            path
            for path in candidates
            if not any(path == guard or guard in path.parents for guard in protected)
        ]
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        try:
            shutil.rmtree(path)
        except OSError:
            pass


@contextmanager
def _isolated_store_attempt(store: MSMemoryStore):
    """Protect the last committed native store state during one attempt.

    Mem0 and A-MEM mutate databases/directories before the parent SQL
    transaction knows whether the logical write succeeded.  A small backup of
    the store-local mutable files gives a failed attempt transactional
    semantics without asking either upstream framework to implement rollback.
    The embedding cache is study-shared and append-only, so it is intentionally
    outside this backup.
    """
    if not store.snapshot_path:
        yield
        return
    committed_path = Path(store.snapshot_path)
    committed_root = committed_path.parent
    allowed_root = (
        Path(settings.MEMORY_STUDY_ARTIFACT_ROOT).resolve() / store.study_id / "memory"
    ).resolve()
    resolved_committed_root = committed_root.resolve()
    if (
        resolved_committed_root == allowed_root
        or not resolved_committed_root.is_relative_to(allowed_root)
    ):
        raise MemoryFrameworkError(
            "memory store snapshot path is outside the study artifact root"
        )
    committed_root.mkdir(parents=True, exist_ok=True)
    attempt_parent = Path(
        tempfile.mkdtemp(prefix=f"attempt-{store.id}-", dir=committed_root.parent)
    )
    attempt_root = attempt_parent / "store"
    try:
        attempt_root.mkdir()
        # Copy the complete stream directory, not just framework-known files:
        # an upstream package may add a sidecar file in a future pinned
        # revision.  A copy failure is still an attempt failure, so clean the
        # staging directory before propagating it to the ledger.
        for child in committed_root.iterdir():
            destination = attempt_root / child.name
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)
    except BaseException:
        shutil.rmtree(attempt_parent, ignore_errors=True)
        raise
    original_snapshot_path = store.snapshot_path
    original_metadata = {
        name: getattr(store, name)
        for name in (
            "snapshot_json",
            "snapshot_sha256",
            "snapshot_artifact_sha256",
            "framework_version",
            "snapshot_stream_id",
            "last_call_id",
            "committed_count",
            "history_count",
            "status",
        )
    }
    store.snapshot_path = str(attempt_root / "snapshot.json")
    try:
        yield
    except BaseException:
        store.snapshot_path = original_snapshot_path
        for name, value in original_metadata.items():
            setattr(store, name, value)
        raise
    else:
        publish_backup = Path(
            tempfile.mkdtemp(prefix=f"publish-{store.id}-", dir=committed_root.parent)
        )
        moved_attempt_names: list[str] = []
        try:
            # The snapshot's native-artifact entries are emitted while the
            # adapter points at ``attempt_root``.  Rewrite those paths before
            # promotion so the committed DB/file snapshot describes the
            # durable stream rather than a directory that is about to be
            # removed.
            if isinstance(store.snapshot_json, dict):
                relocated = _relocate_paths(
                    store.snapshot_json, attempt_root, committed_root
                )
                if relocated != store.snapshot_json:
                    store.snapshot_json = relocated
                    store.snapshot_sha256 = memory_hash(relocated)
                    _atomic_snapshot(str(attempt_root / "snapshot.json"), relocated)
            # Publish the complete attempt only after the framework operation
            # and snapshot serialization both returned successfully.  Each
            # child move is same-filesystem atomic; the parent SQL transaction
            # then commits the matching DB metadata.
            for child in list(committed_root.iterdir()):
                os.replace(child, publish_backup / child.name)
            for child in list(attempt_root.iterdir()):
                # Record the target before the replace.  If the filesystem
                # raises after installing a child, rollback can remove only
                # the newly published names; an exception before this point
                # must leave the original committed stream untouched.
                moved_attempt_names.append(child.name)
                os.replace(child, committed_root / child.name)
        except BaseException:
            # Do not wipe the committed directory when serialization fails
            # before the first original child is moved to the backup.  The
            # old implementation did exactly that, turning a harmless temp
            # write error into permanent loss of the last good snapshot.
            for name in moved_attempt_names:
                child = committed_root / name
                if not child.exists() and not child.is_symlink():
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
            for child in publish_backup.iterdir():
                os.replace(child, committed_root / child.name)
            store.snapshot_path = original_snapshot_path
            for name, value in original_metadata.items():
                setattr(store, name, value)
            raise
        shutil.rmtree(publish_backup, ignore_errors=True)
        store.snapshot_path = original_snapshot_path
    finally:
        shutil.rmtree(attempt_parent, ignore_errors=True)


def _messages(system: str, payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    return (
        {"role": "system", "content": system},
        {"role": "user", "content": _json(payload)},
    )


def _score_messages(
    call: MSCall, record: dict[str, Any], retrieval: list[dict[str, Any]]
) -> tuple[dict[str, str], ...]:
    if record.get("protocol_id") == V3_R2_PROTOCOL_ID:
        return _messages(
            V3_R2_SCORING_SYSTEM_PROMPT,
            {
                "question": record["question_text"],
                "reference_answer": record["reference_answer"],
                "answer": record["student_answer"],
                "score_floor": record["score_floor"],
                "score_ceiling": record["score_ceiling"],
                "memory": retrieval,
            },
        )
    return _messages(
        LEGACY_SCORING_SYSTEM_PROMPT,
        {
            "question": record["question_text"],
            "reference_answer": record["reference_answer"],
            "answer": record["student_answer"],
            "max_score": record["max_score"],
            "memory": retrieval,
            "condition": call.condition,
        },
    )


def _preview(value: Any, limit: int = 200) -> str:
    """Return a single-line, bounded, printable excerpt for a failure message."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return repr(text)


def _minimal_memory_case(item: Any, *, framework: str = "unknown") -> dict[str, Any]:
    """Project retrieved evidence into the V3-r2 student-visible shape.

    Framework IDs, ranks, similarities and native envelopes are retained in
    the audit column but never sent to the scorer. The adapters use the V3
    legacy case keys; the nested-envelope handling keeps this projection
    robust across the official framework adapters.

    Failures raise :class:`MemoryEvidenceError` naming the framework, the
    stored answer and a bounded excerpt of what was actually returned.  A
    projection failure is a data problem, and the excerpt is the only way to
    tell a misconfigured adapter apart from a framework that stored its own
    rewritten prose -- the two used to collapse into the same
    ``framework_unavailable`` "retrieval case is not valid scoring JSON".
    """
    answer_id = getattr(item, "answer_id", "<unknown>")
    payload = item.payload if isinstance(item.payload, dict) else {}
    candidate: Any = payload
    for key in ("content", "memory", "text", "document"):
        nested = payload.get(key)
        if nested is None:
            continue
        candidate = nested
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise MemoryEvidenceError(
                    f"{framework} memory {answer_id} is not canonical scoring "
                    f"JSON: the stored evidence cannot be projected to "
                    f"student_answer/teacher_score. Got {_preview(candidate)}"
                ) from exc
        break
    if not isinstance(candidate, dict):
        raise MemoryEvidenceError(
            f"{framework} memory {answer_id} is not a JSON object: "
            f"got {_preview(candidate)}"
        )
    answer = candidate.get("answer", candidate.get("student_answer"))
    score = candidate.get("manual_score", candidate.get("teacher_score"))
    if answer is None or score is None:
        raise MemoryEvidenceError(
            f"{framework} memory {answer_id} lacks the required scoring "
            f"evidence (student answer and teacher score); keys present: "
            f"{sorted(candidate)}"
        )
    visible = {"student_answer": answer, "teacher_score": score}
    feedback = candidate.get("manual_feedback", candidate.get("teacher_feedback"))
    if feedback is not None:
        visible["teacher_feedback"] = feedback
    return visible


def _question_score_ceiling(study, question_id: str) -> float:
    """The frozen per-question score ceiling (falls back to the legacy
    training-observed max for studies built before score_ceilings existed)."""
    manifest = study.data_manifest_json or {}
    ceilings = manifest.get("score_ceilings") or {}
    if question_id in ceilings:
        return float(ceilings[question_id])
    training_max = manifest.get("max_scores_from_training") or {}
    return float(training_max[question_id])


def _question_score_floor(study, question_id: str) -> float:
    """Return the frozen per-question lower score bound."""
    manifest = study.data_manifest_json or {}
    floors = manifest.get("score_floors") or {}
    if question_id in floors:
        return float(floors[question_id])
    return 0.0


def _record(db: Session, study_id: str, answer_id: str) -> dict[str, Any]:
    from app.models.memory_study import MSRecord

    row = db.scalar(
        select(MSRecord).where(
            MSRecord.study_id == study_id, MSRecord.answer_id == answer_id
        )
    )
    if row is None:
        raise RuntimeError(f"memory-study record missing: {answer_id}")
    study = db.get(MSStudy, study_id)
    assert study is not None
    result = {
        "answer_id": row.answer_id,
        "question_id": row.question_id,
        "question_text": row.question_text,
        "reference_answer": row.reference_answer,
        "student_answer": row.student_answer,
        "teacher_score": row.teacher_score,
        "teacher_feedback": row.teacher_feedback,
        "max_score": _question_score_ceiling(study, row.question_id),
        "score_floor": _question_score_floor(study, row.question_id),
        "score_ceiling": _question_score_ceiling(study, row.question_id),
        "protocol_id": study.protocol_id,
    }
    return result


def _embedding_provider(study: MSStudy):
    root = Path(settings.MEMORY_STUDY_ARTIFACT_ROOT) / study.id / "embeddings"
    if study.config_json.get("embedding_backend") != "openai":
        raise MemoryFrameworkError(
            "memory-study retrieval requires the OpenAI-compatible API embedding backend"
        )
    return OpenAICompatibleEmbeddingProvider(
        model_name=study.config_json["embedding_model"],
        revision=study.config_json["embedding_revision"],
        api_base=study.config_json["embedding_api_base"],
        api_key=study.config_json["embedding_api_key"],
        cache_dir=root,
    )


def _runtime_for_model(db: Session, study: MSStudy, model: str) -> dict[str, Any]:
    """Resolve the per-model runtime for one study.

    The project binds each model to a ``speed_mode`` (frozen in
    ``config_json["models"]`` at creation).  The matching site-level runner
    config row ``(model, bound_speed_mode)`` overrides the frozen parameters
    when present; otherwise the frozen defaults apply.  A project binding
    never falls back to the other speed mode, so each study is reproducible
    from its own frozen configuration."""
    # A frozen study owns its runtime.  Site-level runner rows are consulted
    # only while the project is created/frozen; reading them here used to make
    # an operator edit silently change a running experiment halfway through.
    frozen = study.config_json.get("runtime_snapshot", {})
    if isinstance(frozen, dict) and isinstance(frozen.get(model), dict):
        runtime = dict(frozen[model])
        runtime.setdefault("model", model)
        runtime.setdefault("speed_mode", "standard")
        runtime.setdefault(
            "service_tier", "fast" if runtime["speed_mode"] == "fast" else "default"
        )
        return runtime
    for configured in study.config_json.get("models", []):
        if configured.get("model") == model:
            base = {
                "model": model,
                "reasoning_effort": configured["reasoning_effort"],
                "speed_mode": configured.get("speed_mode", "standard"),
                "timeout_seconds": configured.get(
                    "timeout_seconds", INITIAL_TIMEOUT_SECONDS
                ),
            }
            base["service_tier"] = "fast" if base["speed_mode"] == "fast" else "default"
            # Legacy rows created before runtime_snapshot was introduced are
            # still readable.  Their effective site row is resolved only for
            # that legacy shape; every current study has a snapshot and never
            # consults mutable site configuration while running.
            if db is not None:
                from app.models.memory_study import MSRunnerConfig

                configured_row = db.scalar(
                    select(MSRunnerConfig).where(
                        MSRunnerConfig.model == model,
                        MSRunnerConfig.speed_mode == base["speed_mode"],
                    )
                )
                if configured_row is not None:
                    base["reasoning_effort"] = configured_row.reasoning_effort
                    base["timeout_seconds"] = configured_row.timeout_seconds
            return base
    raise RuntimeError(f"model {model} is not present in the frozen study config")


def _assert_runner_runtime(runner: Any, runtime: dict[str, Any]) -> None:
    """Reject a CLI that changed after preflight, before issuing a call."""
    method = getattr(runner, "assert_matches", None)
    if callable(method) and any(
        runtime.get(key)
        for key in ("executable_path", "executable_sha256", "cli_version")
    ):
        # ``assert_matches`` performs a fresh ``codex --version`` check.  A
        # slot can issue thousands of logical calls, so perform that expensive
        # drift check once per runner after preflight rather than spawning a
        # diagnostic subprocess on every call.  The next worker/preflight gets
        # a new runner and repeats the check.
        if getattr(runner, "_memory_study_runtime_verified", False):
            return
        method(runtime)
        try:
            setattr(runner, "_memory_study_runtime_verified", True)
        except Exception:
            # A minimal fake/adapter may use ``__slots__``; the assertion still
            # happened, and failing to memoize it must not fail the experiment.
            pass


def _snapshot_is_valid(store: MSMemoryStore) -> bool:
    """Validate the DB snapshot, file snapshot, and native artifact hash."""
    if store.snapshot_json is None:
        if store.committed_count != 0:
            return False
        if not store.snapshot_path:
            return True
        snapshot_path = Path(store.snapshot_path)
        if snapshot_path.exists():
            return False
        stream_root = snapshot_path.parent
        if stream_root.is_dir():
            try:
                return not any(stream_root.iterdir())
            except OSError:
                return False
        return True
    if not store.snapshot_path:
        return False
    path = Path(store.snapshot_path)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload != store.snapshot_json or memory_hash(payload) != store.snapshot_sha256:
        return False
    artifact_hash = payload.get("artifact_sha256") or store.snapshot_artifact_sha256
    if artifact_hash:
        artifact_dir = path.parent / "native-artifacts"
        if not artifact_dir.is_dir() or _sha256_path(artifact_dir) != artifact_hash:
            return False
    return True


def _prepare_empty_store_for_write(store: MSMemoryStore) -> None:
    """Remove framework initialization residue before the first commit.

    A-MEM/Chroma can create vector-store files while serving the first
    training score, before the corresponding memory write has committed a
    snapshot.  An empty logical store therefore may have an on-disk directory
    even though it has no valid snapshot yet.  Those files are disposable:
    there is no committed history to preserve, and retaining them would make
    the next write fail validation or layer data on an unknown state.
    """
    if store.snapshot_json is not None or store.committed_count != 0:
        return
    if not store.snapshot_path:
        return
    root = Path(store.snapshot_path).resolve().parent
    allowed_root = (
        Path(settings.MEMORY_STUDY_ARTIFACT_ROOT).resolve() / store.study_id / "memory"
    ).resolve()
    if root == allowed_root or not root.is_relative_to(allowed_root):
        raise MemoryFrameworkError(
            "memory store snapshot path is outside the study artifact root"
        )
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


@contextmanager
def _isolated_store_read(store: MSMemoryStore):
    """Read a native store from a disposable copy.

    Chroma may update SQLite metadata even for a query.  Keeping that write
    on the committed directory races with the atomic memory-write publisher
    and can produce ``readonly database`` errors.  Scores never commit memory
    state, so a throw-away copy is the correct isolation boundary.
    """
    if not store.snapshot_path:
        yield
        return
    committed_root = Path(store.snapshot_path).resolve().parent
    # Formal v3 creates empty stores before their first memory write.  Such a
    # stream has a snapshot path in the database but no artifact directory on
    # disk yet.  ``mkdtemp(..., dir=...)`` requires its parent to exist, so
    # create the condition directory before opening the disposable read copy.
    committed_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f"score-{store.id}-", dir=committed_root.parent)
    )
    read_root = temporary / "store"
    read_root.mkdir()
    try:
        if committed_root.is_dir():
            for child in committed_root.iterdir():
                destination = read_root / child.name
                if child.is_dir():
                    shutil.copytree(child, destination)
                else:
                    shutil.copy2(child, destination)
        original_path = store.snapshot_path
        original_snapshot = store.snapshot_json
        store.snapshot_path = str(read_root / "snapshot.json")
        if isinstance(original_snapshot, dict):
            store.snapshot_json = _relocate_paths(
                original_snapshot, committed_root, read_root
            )
        try:
            yield
        finally:
            store.snapshot_path = original_path
            store.snapshot_json = original_snapshot
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _stream_id(store: MSMemoryStore) -> str:
    return store.snapshot_stream_id or (
        f"{store.study_id}:{store.model}:{store.question_id}:"
        f"{store.condition}:{store.order_variant}"
    )


def _framework_config(study: MSStudy, store: MSMemoryStore) -> dict[str, Any]:
    """Build a per-stream official config; streams never share local stores."""
    root = Path(
        store.snapshot_path or (Path(settings.MEMORY_STUDY_ARTIFACT_ROOT) / study.id)
    )
    root = root.parent if root.name == "snapshot.json" else root
    stream_id = _stream_id(store)
    if store.framework == "mem0":
        embedder = (
            {
                "provider": "openai",
                "config": {
                    "model": study.config_json["embedding_model"],
                    "api_key": study.config_json.get("embedding_api_key", ""),
                    "openai_base_url": study.config_json.get("embedding_api_base", ""),
                },
            }
            if study.config_json.get("embedding_backend") == "openai"
            else {
                "provider": "huggingface",
                "config": {
                    "model": study.config_json["embedding_model"],
                    "model_kwargs": {
                        "revision": study.config_json["embedding_revision"]
                    },
                },
            }
        )
        return {
            "stream_id": stream_id,
            "snapshot_root": str(root),
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"saf_{stream_id[:24]}",
                    "path": str(root / "vector"),
                },
            },
            "embedder": embedder,
            "llm": {
                # ``litellm`` passes mem0's whitelisted-provider validation and
                # constructs without a network call; the Codex bridge replaces
                # the object right after construction.
                "provider": "litellm",
                "config": {
                    "model": store.model,
                    "api_key": "codex-exec-bridge-disabled",
                },
            },
            "history_db_path": str(root / "history.db"),
            "version": "v1.1",
        }
    return {
        "stream_id": stream_id,
        "snapshot_root": str(root),
        "collection_name": f"saf_{stream_id[:24]}",
        "model_name": study.config_json["embedding_model"],
        "embedding_revision": study.config_json["embedding_revision"],
        # The adapter's A-MEM backend validates the study-wide embedding
        # backend; the frozen study config resolved it at creation.
        "embedding_backend": study.config_json.get("embedding_backend", ""),
        "embedding_model": study.config_json["embedding_model"],
        "embedding_api_base": study.config_json.get("embedding_api_base", ""),
        "embedding_api_key": study.config_json.get("embedding_api_key", ""),
        "llm_model": store.model,
        "llm_backend": "sglang",
        "api_key": "codex-exec-bridge-disabled",
    }


def _adapter(
    db: Session,
    study: MSStudy,
    store: MSMemoryStore,
    *,
    runner: Any | None = None,
    runtime: dict[str, Any] | None = None,
    recorder: FrameworkInvocationRecorder | None = None,
):
    from app.models.memory_study import MSRecord

    catalog_rows = list(
        db.scalars(
            select(MSRecord).where(
                MSRecord.study_id == study.id,
                MSRecord.question_id == store.question_id,
                MSRecord.selection_kind == "training",
            )
        )
    )
    catalog = [
        type(
            "StudyRecordProxy",
            (),
            {
                "answer_id": row.answer_id,
                "question_id": row.question_id,
                "question_text": row.question_text,
                "reference_answer": row.reference_answer,
                "student_answer": row.student_answer,
                "teacher_score": row.teacher_score,
                "teacher_feedback": row.teacher_feedback,
                "verification_feedback": row.verification_feedback,
                "source_split": row.source_split,
                "source_path": row.source_path,
                "source_position": row.source_position,
                "excluded_reason": None,
                "selection_kind": row.selection_kind,
            },
        )()
        for row in catalog_rows
    ]
    memory_profile = memory_profile_for_protocol(study.protocol_id)
    if store.framework == "retrieval":
        provider = _embedding_provider(study)
        adapter = create_adapter(
            framework="retrieval",
            feedback_mode=store.feedback_mode,
            provider=provider,
            catalog=catalog,
            memory_profile=memory_profile,
        )
    else:
        adapter = create_adapter(
            framework=store.framework,
            feedback_mode=store.feedback_mode,
            config=_framework_config(study, store),
            runner=runner,
            runtime=runtime,
            recorder=recorder,
            snapshot_root=(
                Path(store.snapshot_path).parent if store.snapshot_path else None
            ),
            stream_id=_stream_id(store),
            memory_profile=memory_profile,
        )
    snapshot = store.snapshot_json
    if snapshot is None and store.snapshot_path and Path(store.snapshot_path).is_file():
        snapshot = json.loads(Path(store.snapshot_path).read_text(encoding="utf-8"))
    if snapshot:
        adapter.restore(snapshot)
    return adapter


def _all_memory_resolved(
    db: Session, study_id: str, *, model: str | None = None
) -> bool:
    """Return True once one model's stores reached a terminal state.

    ``model`` is supplied by the fixed condition slots. Keeping the optional
    argument preserves the old global predicate for callers that only need a
    study-wide audit, while execution uses a model-local phase barrier.
    """
    predicate = [MSMemoryStore.study_id == study_id]
    if model is not None:
        predicate.append(MSMemoryStore.model == model)
    stores = list(db.scalars(select(MSMemoryStore).where(*predicate)))
    if not stores:
        return False
    return all(store.status in STORE_TERMINAL_STATUSES for store in stores)


def _all_memory_completed(
    db: Session,
    study_id: str,
    *,
    model: str | None = None,
    question_id: str | None = None,
    feedback_mode: str | None = None,
) -> bool:
    """Return True only when every store for a model has a committed snapshot.

    ``_all_memory_resolved`` intentionally includes failed stores for audit
    and recovery callers.  Scheduling a score is stricter: a failed store is
    a terminal error, not a valid training barrier, so its score queue must
    remain blocked until the store is repaired and reaches ``completed``.

    ``feedback_mode`` narrows the barrier to one wave.  The full-feedback
    wave's test barrier used to span both feedback modes, so the still-pending
    no_feedback stores permanently blocked full-wave test scores while the
    wave ordering itself blocked no_feedback training writes -- a circular
    wait that stalled the whole study.
    """
    predicate = [MSMemoryStore.study_id == study_id]
    if model is not None:
        predicate.append(MSMemoryStore.model == model)
    if question_id is not None:
        predicate.append(MSMemoryStore.question_id == question_id)
    if feedback_mode is not None:
        predicate.append(MSMemoryStore.feedback_mode == feedback_mode)
    stores = list(db.scalars(select(MSMemoryStore).where(*predicate)))
    if not stores:
        return False
    return all(store.status == "completed" for store in stores)


def reclaim_expired_calls(db: Session, study_id: str) -> int:
    """Reset calls whose lease expired after a crash or hot reload.

    Expired ``leased``/``running`` rows are unreachable for both the claim
    query (pending-only) and the manual retry endpoint (failed-only), so
    without this sweep one dead worker can freeze the study forever.  Each
    reclaim writes a failed attempt row so the ledger stays complete.

    A call whose call-level lease lapsed while its owner worker is still
    heartbeating must NOT be reclaimed: the slot is simply mid-flight on a
    slow framework call.  Reclaiming it would requeue a call the live slot is
    still executing, duplicate the same attempt row, and violate
    ``uq_ms_call_attempt``.
    """
    now = utc_now_naive()
    runtime = db.get(MSSchedulerRuntime, 1)
    owner_online = (
        runtime is not None
        and runtime.owner_id
        and runtime.lease_until is not None
        and runtime.lease_until >= now
    )
    expired = list(
        db.scalars(
            select(MSCall)
            .where(
                MSCall.study_id == study_id,
                MSCall.status.in_([CallStatus.leased, CallStatus.running]),
                MSCall.lease_until.is_not(None),
                MSCall.lease_until < now,
            )
            .with_for_update(skip_locked=True)
        )
    )
    reclaimed = 0
    for call in expired:
        if owner_online and call.worker_id == runtime.owner_id:
            # The live worker may have exceeded the per-call lease on a long
            # framework invocation; give the existing slot time to finish.
            continue
        reclaimed += 1
        call.status = CallStatus.pending
        call.lease_until = None
        call.slot_id = None
        # The slot that still owns this call may be mid-flight and have just
        # written an attempt row under the same (call_id, attempt_number).
        # Reclaiming must never duplicate that row or `uq_ms_call_attempt`
        # rejects the write and kills the worker.  Only record the expired
        # lease when no attempt exists yet for the current execution.
        attempt_exists = db.scalar(
            select(func.count(MSCallAttempt.id)).where(
                MSCallAttempt.call_id == call.id,
                MSCallAttempt.attempt_number == call.attempt_count,
            )
        )
        if not attempt_exists:
            db.add(
                MSCallAttempt(
                    call_id=call.id,
                    attempt_number=call.attempt_count,
                    request_sha256=_call_request_sha(call),
                    status="failed",
                    requested_model=call.model,
                    error_type="lease_expired",
                    error_message="worker lease expired; call returned to the queue",
                    started_at=call.started_at or now,
                )
            )
        # 与终态失败不同，租约过期回到 pending 的调用必须清空失败码，
        # 否则旧的 transient/lease_expired 码会被误读为一次已决的终态失败，
        # 让 attempt 预算与自动退避判断失真。
        call.failure_code = None
        call.failure_summary = None
    if reclaimed:
        db.commit()
    else:
        # Nothing changed; drop the snapshot so the transaction does not hold
        # a stale view for the caller's next query.
        db.rollback()
    return reclaimed


def _defer_call_for_backoff(
    db: Session, call: MSCall, *, retry_after: datetime
) -> None:
    """Requeue a transiently failed call with exponential-backoff delay.

    The failed attempt row has already been committed by ``_execute_call``;
    this requeues the *logical* call as ``pending`` with a ``retry_after``
    timestamp so the slots keep serving other shards in the meantime.  The
    attempt ledger stays the single source of truth: a later claim re-runs
    the call with a fresh attempt number and budget.
    """
    call.status = CallStatus.pending
    call.lease_until = None
    call.slot_id = None
    call.retry_after = retry_after
    call.failure_code = None
    call.failure_summary = None


def _next_backoff_seconds(failure_count: int) -> float:
    """Exponential backoff for deferred retries, capped at the maximum.

    ``failure_count`` is the number of *failed attempts* already recorded for
    the call, so the first deferral waits one minute, the second two, and so
    on until the cap.
    """
    delay = TRANSIENT_RETRY_BASE_SECONDS * (2 ** max(0, failure_count - 1))
    return float(min(delay, TRANSIENT_RETRY_MAX_SECONDS))


def _question_is_runnable(db: Session, study_id: str, question_id: str) -> bool:
    """Question shards are independent failure/pause barriers."""
    shard = db.scalar(
        select(MSQuestionRun).where(
            MSQuestionRun.study_id == study_id,
            MSQuestionRun.question_id == question_id,
        )
    )
    return shard is None or shard.status == "running"


def _training_complete_for_call(db: Session, call: MSCall) -> bool:
    """Test scores open only after this model/question's stores are complete."""
    from app.models.memory_study import MSRecord

    record = db.scalar(
        select(MSRecord).where(
            MSRecord.study_id == call.study_id,
            MSRecord.answer_id == call.answer_id,
        )
    )
    if record is None or record.selection_kind != "test":
        return True
    # The no-memory baseline has no mutable store and therefore has no
    # training-write barrier.  Applying the store predicate to it would leave
    # every baseline test score permanently pending because the store query is
    # intentionally empty.
    if call.condition == "no_memory":
        return True
    return _all_memory_completed(
        db,
        call.study_id,
        model=call.model,
        question_id=call.question_id,
        # 波次隔离：full 波次的测试屏障只看 full store。no_feedback store
        # 尚未训练是设计内状态，与另一波次的测试评分互不构成阻塞。
        feedback_mode=call.feedback_mode,
    )


def claim_next_call(
    db: Session,
    study_id: str,
    *,
    worker_id: str,
    slot_id: str,
    model: str | None = None,
    condition: str | None = None,
) -> MSCall | None:
    """Claim the next call for one fixed model/condition stream.

    A failed or in-flight predecessor blocks only the same question/model/
    condition/order stream.  This is what lets one question enter
    ``attention_required`` without starving the other five shards.
    """
    study = db.get(MSStudy, study_id)
    if study is None or study.status != StudyStatus.running:
        return None
    spec = slot_specs(protocol_id=study.protocol_id).get(slot_id)
    if spec is not None:
        expected_model, expected_condition = spec
        if model is None:
            model = expected_model
        if condition is None:
            condition = expected_condition
        allowed_conditions = {expected_condition}
        if expected_condition.endswith("_full"):
            allowed_conditions.add(
                expected_condition.removesuffix("_full") + "_no_feedback"
            )
        if model != expected_model or condition not in allowed_conditions:
            return None
    expected_model = SLOT_MODELS.get(slot_id)
    if expected_model is not None:
        if model is None:
            model = expected_model
        elif model != expected_model:
            return None
    if study.protocol_id in SUPPORTED_PROTOCOL_IDS:
        configured_models = {
            str(item.get("model"))
            for item in study.config_json.get("models", [])
            if isinstance(item, dict) and item.get("model")
        }
        # A current v3 queue can never be claimed by a model outside its
        # frozen Luna-only configuration. Legacy projects retain their
        # historical claim semantics for read-only inspection/recovery.
        if model is None or model not in configured_models or model not in MODEL_IDS:
            return None
    # The optional no-feedback wave is serialized behind the complete
    # full-feedback wave of the same question shard (checked per candidate
    # call below).  Serializing study-wide used to idle the no-feedback
    # slots whenever any other shard still had full calls left; per-shard
    # gating keeps the wave order inside each stream while letting other
    # shards progress.
    runtime = db.get(MSSchedulerRuntime, 1)
    if runtime is not None and runtime.owner_id:
        lease_live = bool(
            runtime.lease_until is not None and runtime.lease_until >= utc_now_naive()
        )
        # A worker may claim only while it owns the cross-process lease.  The
        # fallback for a missing runtime row keeps direct queue diagnostics and
        # legacy one-off tests usable; real workers always acquire this row
        # before launching either fixed slot.
        if runtime.owner_id != worker_id or not lease_live:
            return None
    reclaim_expired_calls(db, study_id)
    # Shard-aware claiming: only calls whose question shard is currently
    # running are eligible.  Filtering inside the query (rather than sampling
    # a head window by call id) matters because freeze materialises the six
    # shards' calls interleaved by id; a late-launched shard's calls can sit
    # arbitrarily deep in the id order and must still be claimable.  A study
    # without shard rows at all is legacy and stays fully claimable.
    shard_count = db.scalar(
        select(func.count(MSQuestionRun.id)).where(MSQuestionRun.study_id == study_id)
    )
    runnable_filters = []
    if shard_count:
        runnable_question_ids = list(
            db.scalars(
                select(MSQuestionRun.question_id).where(
                    MSQuestionRun.study_id == study_id,
                    MSQuestionRun.status == "running",
                )
            )
        )
        if not runnable_question_ids:
            return None
        runnable_filters.append(MSCall.question_id.in_(runnable_question_ids))
    pending = (
        select(MSCall)
        .where(
            MSCall.study_id == study_id,
            *([MSCall.model == model] if model is not None else []),
            *([MSCall.condition == condition] if condition is not None else []),
            MSCall.status == CallStatus.pending,
            # 指数退避中的调用在其 retry_after 之前不可领取；已到期的
            # 自然重新进入候选。列为 NULL 的普通 pending 不受影响。
            *([MSCall.retry_after.is_(None) | (MSCall.retry_after <= utc_now_naive())]),
            *runnable_filters,
        )
        .order_by(MSCall.id)
    )
    # Scan the pending queue in bounded batches.  A single fixed head window
    # deadlocks whenever the head is dense with calls a gate must skip (for
    # example a partially launched study whose only running shard's test
    # scores sit behind thousands of wave-blocked no_feedback rows): every
    # claim pass re-scans the same unclaimable head and the deeper eligible
    # calls are never reached.  Batching walks past them.
    eligible: list[MSCall] = []
    seen_ids: set[int] = set()
    last_id = 0
    for _batch in range(32):
        batch_query = pending.where(MSCall.id > last_id)
        batch = list(db.scalars(batch_query.limit(256)))
        if not batch:
            break
        last_id = batch[-1].id
        for call in batch:
            if call.id in seen_ids:
                continue
            seen_ids.add(call.id)
            if shard_count and not _question_is_runnable(
                db, study_id, call.question_id
            ):
                continue
            if not _training_complete_for_call(db, call):
                continue
            if condition and condition.endswith("_no_feedback"):
                # This shard's full wave must have fully drained before its
                # no-feedback calls may run.
                full_wave_pending = db.scalar(
                    select(func.count(MSCall.id)).where(
                        MSCall.study_id == study_id,
                        MSCall.model == model,
                        MSCall.question_id == call.question_id,
                        MSCall.condition.in_(_full_wave_conditions(study.protocol_id)),
                        MSCall.status.in_(
                            [CallStatus.pending, CallStatus.leased, CallStatus.running]
                        ),
                    )
                )
                if full_wave_pending:
                    continue
            if call.kind == CallKind.memory_write:
                if call.memory_store_id is None:
                    continue
                store = db.get(MSMemoryStore, call.memory_store_id)
                if store is None or store.status in {
                    "failed",
                    "attention_required",
                    "completed",
                }:
                    continue
            eligible.append(call)
        if eligible:
            # 本批已找到可领取的调用；不再向更深处扫描。
            break
    # A question shard should not monopolise a condition slot simply because
    # its calls were materialised first.  Prefer the question with the least
    # completed work in this model/condition stream, then its stable call id.
    question_progress = {
        question_id: int(
            db.scalar(
                select(func.count(MSCall.id)).where(
                    MSCall.study_id == study_id,
                    MSCall.model == model,
                    *([MSCall.condition == condition] if condition is not None else []),
                    MSCall.question_id == question_id,
                    MSCall.status == CallStatus.succeeded,
                )
            )
            or 0
        )
        for question_id in {call.question_id for call in eligible}
    }
    for call in sorted(
        eligible, key=lambda row: (question_progress.get(row.question_id, 0), row.id)
    ):
        claimed = (
            select(MSCall)
            .where(MSCall.id == call.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        call = db.scalar(claimed)
        if call is None or call.status != CallStatus.pending:
            continue
        current_study_status = db.scalar(
            select(MSStudy.status).where(MSStudy.id == study_id)
        )
        if current_study_status != StudyStatus.running:
            return None
        # Re-check the ordered prefix after taking the row lock.  A second
        # claimant may have read the same pending list before the first one
        # committed; without this guard it could immediately skip the newly
        # leased head and run a later call for the same model in parallel.
        blocker = db.scalar(
            select(MSCall.id)
            .where(
                MSCall.study_id == study_id,
                *([MSCall.model == model] if model is not None else []),
                *([MSCall.condition == condition] if condition is not None else []),
                MSCall.question_id == call.question_id,
                MSCall.order_variant == call.order_variant,
                MSCall.id <= call.id,
                MSCall.status.in_(
                    [CallStatus.leased, CallStatus.running, CallStatus.failed]
                ),
            )
            .limit(1)
        )
        if blocker is not None:
            # Another slot may be holding the head of this shard.  Try the
            # next question rather than idling the whole condition slot.
            continue
        call.status = CallStatus.leased
        call.worker_id = worker_id
        call.slot_id = slot_id
        call.lease_until = utc_now_naive() + timedelta(seconds=LEASE_SECONDS)
        call.retry_after = None
        call.started_at = utc_now_naive()
        db.commit()
        return call
    return None


class MemoryStudyWorker:
    """Run the frozen queue through one serial slot per configured model."""

    def __init__(self, *, runner_factory: Callable[[], Any] | None = None):
        self.runner_factory = runner_factory or CodexExecRunner
        self._control_lock = Lock()
        self._stop_events: dict[str, Event] = {}
        self._runners: dict[str, list[Any]] = {}

    def request_stop(self, study_id: str, *, terminate: bool = False) -> None:
        """Stop issuing work; optionally terminate active Codex children."""
        with self._control_lock:
            event = self._stop_events.get(study_id)
            if event is not None:
                event.set()
            if terminate:
                for runner in self._runners.get(study_id, []):
                    terminate_active = getattr(runner, "terminate_active", None)
                    if callable(terminate_active):
                        try:
                            terminate_active(interrupted=True)
                        except Exception:
                            pass

    def run(self, study_id: str, *, worker_id: str | None = None) -> None:
        worker_id = worker_id or f"memory-study-{uuid4().hex[:12]}"
        with SessionLocal() as db:
            study = db.get(MSStudy, study_id)
            if study is None:
                raise RuntimeError("memory-study project not found")
            if study.protocol_id not in {PROTOCOL_ID, DEFAULT_PROTOCOL_ID}:
                raise RuntimeError(
                    "memory-study worker only runs the supported Luna-only V3 protocol"
                )
            if study.status != StudyStatus.running:
                raise RuntimeError(
                    "memory-study worker can only run a project already marked running"
                )
            configured_models = [
                str(item.get("model")) for item in study.config_json.get("models", [])
            ]
            if configured_models != list(MODEL_IDS):
                raise RuntimeError(
                    "memory-study frozen model slots must match the configured Luna-only order"
                )
            if study.config_json.get("embedding_revision") in {
                "",
                "main",
                "latest",
                "unresolved",
            }:
                raise RuntimeError(
                    "memory-study embedding revision must be immutable before a real run"
                )
            if study.config_json.get("embedding_backend") != "openai":
                raise RuntimeError(
                    "memory-study requires API embedding for every retrieval condition"
                )
            # The CLI worker is also an entry point, so enforce the same
            # immutable preflight gate as the HTTP start endpoint.  Importing
            # lazily avoids the service/worker module cycle at import time.
            from app.services.memory_study import MemoryStudyService

            fresh, reason = MemoryStudyService(
                db, runner_factory=self.runner_factory
            )._preflight_is_fresh(study)
            if not fresh:
                raise RuntimeError(reason or "runtime preflight required")
        try:
            token_count("SAF memory-study tokenizer probe")
            verify_official_memory_dependencies()
        except MemoryFrameworkError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"memory-study runtime verification failed: {exc}"
            ) from exc
        # Derive slots from the immutable project snapshot rather than from
        # mutable site configuration or a positional model constant.
        slot_map = slot_specs(tuple(configured_models), study.protocol_id)
        self._acquire_runtime(worker_id, study_id, slot_models=slot_map)
        # Only a worker that owns the runtime lease may clean staging dirs:
        # auto-rescan callers that lose the acquire must not sweep the tree
        # while the current owner's slot threads are mid-framework-op inside
        # their attempt dirs.
        _cleanup_stale_attempt_dirs(study_id)
        # pool.submit() 返回 Future，没有 is_alive()。旧实现把 Future 当线程
        # 存进 slot_threads，每次心跳的存活检查都抛 AttributeError 且被循环的
        # 兜底 except 吞掉——租约从未续期，约 10 分钟后 UI 必报 not connected。
        live: dict[str, Any] = {}
        heartbeat = _HeartbeatThread(
            worker_id,
            liveness_check=lambda: any(
                not future.done() for future in list(live.values())
            ),
        )
        heartbeat.start()
        stop_event = Event()
        with self._control_lock:
            self._stop_events[study_id] = stop_event
            self._runners[study_id] = []
        try:
            with ThreadPoolExecutor(
                max_workers=len(slot_map), thread_name_prefix="memory-study-slot"
            ) as pool:
                # 槽线程由 _run_slot 自身保证不退出（内部已兜底异常）；这里
                # 再加一层对账：如果某个槽线程仍然意外死亡（例如在 finally
                # 之前被解释器级错误打断），主循环重建它，避免出现"心跳在
                # 跳、槽已全灭"的僵尸运行态。全部槽退出且队列仍有待做调用
                # 时同样重建——只有研究不再是 running 或队列真正排干才放行。
                slot_ids = list(slot_map)
                while not stop_event.is_set():
                    for slot_id in slot_ids:
                        future = live.get(slot_id)
                        if future is not None and not future.done():
                            continue
                        if future is not None:
                            # 线程已退出：区分"正常排空"与"意外死亡"。
                            exc = future.exception()
                            if exc is not None:
                                logger.error(
                                    "memory-study slot %s died: %s; respawning",
                                    slot_id,
                                    exc,
                                )
                            else:
                                # _run_slot 只在 stop_event 或研究结束/队列
                                # 排空时返回；正常退出不再重建。
                                continue
                        live[slot_id] = pool.submit(
                            self._run_slot,
                            study_id,
                            worker_id,
                            slot_id,
                            slot_map[slot_id][0],
                            slot_map[slot_id][1],
                            stop_event,
                        )
                    if live and all(
                        future.done() and future.exception() is None
                        for future in live.values()
                    ):
                        # 所有槽都报告"正常退出"：队列排空或研究已终止。
                        break
                    stop_event.wait(30.0)
            self._finalize_study(study_id)
        finally:
            with self._control_lock:
                self._stop_events.pop(study_id, None)
                self._runners.pop(study_id, None)
            heartbeat.stop()
            self._release_runtime(worker_id)

    def _finalize_study(self, study_id: str) -> None:
        """Set the terminal status once every call reached an end state."""
        with SessionLocal() as db:
            study = db.get(MSStudy, study_id)
            if study is None or study.status != StudyStatus.running:
                return
            # Reconcile per-question shard state before deciding whether the
            # parent can be terminal.  A failed shard is isolated; a pending
            # shard simply means the operator has not launched it yet.
            shards = list(
                db.scalars(
                    select(MSQuestionRun).where(MSQuestionRun.study_id == study_id)
                )
            )
            for shard in shards:
                qcounts = dict(
                    db.execute(
                        select(MSCall.status, func.count(MSCall.id))
                        .where(
                            MSCall.study_id == study_id,
                            MSCall.question_id == shard.question_id,
                        )
                        .group_by(MSCall.status)
                    ).all()
                )
                if qcounts.get(CallStatus.failed, 0):
                    shard.status = "attention_required"
                elif not any(
                    qcounts.get(status, 0)
                    for status in (
                        CallStatus.pending,
                        CallStatus.leased,
                        CallStatus.running,
                    )
                ) and shard.status not in {"terminated", "attention_required"}:
                    shard.status = "completed"
                    shard.completed_at = shard.completed_at or utc_now_naive()
            counts = dict(
                db.execute(
                    select(MSCall.status, func.count(MSCall.id))
                    .where(MSCall.study_id == study_id)
                    .group_by(MSCall.status)
                ).all()
            )
            if counts.get(CallStatus.leased) or counts.get(CallStatus.running):
                return
            # Legacy rows created before question shards can still be resumed
            # through the project-level worker.  A pending queue in that shape
            # is unfinished work, not a completed study; retain ``running`` so
            # the caller can reclaim/continue it rather than silently closing
            # the project.
            if not shards and counts.get(CallStatus.pending):
                db.commit()
                return
            from app.services.memory_study import MemoryStudyService

            MemoryStudyService(db).refresh_progress(study_id)
            failed = counts.get(CallStatus.failed, 0)
            unfinished_shards = [
                shard
                for shard in shards
                if shard.status not in {"completed", "terminated", "attention_required"}
            ]
            if failed:
                codes = dict(
                    db.execute(
                        select(MSCall.failure_code, func.count(MSCall.id))
                        .where(
                            MSCall.study_id == study_id,
                            MSCall.status == CallStatus.failed,
                        )
                        .group_by(MSCall.failure_code)
                    ).all()
                )
                summary = "; ".join(
                    f"{code or 'unknown'} x{n}" for code, n in sorted(codes.items())
                )
                study.status = StudyStatus.attention_required
                study.error_summary = (
                    f"{failed} call(s) failed ({summary}); manual retry required"
                )
            elif unfinished_shards:
                study.status = StudyStatus.paused
                study.error_summary = "题目分片尚未全部启动或完成"
            else:
                study.status = StudyStatus.completed
                study.completed_at = utc_now_naive()
                study.error_summary = None
            db.commit()

    def _run_slot(
        self,
        study_id: str,
        worker_id: str,
        slot_id: str,
        model: str | None = None,
        condition: str | None = None,
        stop_event: Event | None = None,
    ) -> None:
        model = model or SLOT_MODELS.get(slot_id)
        if model is None:
            raise RuntimeError(f"unknown memory-study slot: {slot_id}")
        with SessionLocal() as db:
            study = db.get(MSStudy, study_id)
            protocol_id = study.protocol_id if study is not None else PROTOCOL_ID
        condition = condition or (
            slot_specs(protocol_id=protocol_id).get(slot_id) or (model, None)
        )[1]
        stop_event = stop_event or Event()
        runner = None
        try:
            runner = self.runner_factory()
        except Exception:
            runner = None
        if runner is not None:
            with self._control_lock:
                self._runners.setdefault(study_id, []).append(runner)
        self._set_slot_runtime(worker_id, slot_id, state="idle", model=model)
        # A slot must survive its own failures: any unexpected exception in
        # claim/execute is contained to the current call, the slot logs it,
        # reports one failed slot turn, and keeps serving other shards.  The
        # old behavior let one raising slot future tear down the whole worker
        # while the heartbeat thread kept the lease alive -- a zombie run the
        # UI still showed as "running".
        while not stop_event.is_set():
            try:
                if self._run_slot_turn(
                    study_id, worker_id, slot_id, model, condition, stop_event, runner
                ):
                    return
            except Exception:
                logger.exception(
                    "memory-study slot %s crashed; restarting its loop", slot_id
                )
                try:
                    self._set_slot_runtime(
                        worker_id, slot_id, state="idle", model=model
                    )
                except Exception:
                    pass
                stop_event.wait(1.0)

    def _run_slot_turn(
        self,
        study_id: str,
        worker_id: str,
        slot_id: str,
        model: str,
        condition: str | None,
        stop_event: Event,
        runner: Any,
    ) -> bool:
        """Run one claim/execute cycle.  Returns True only to exit the slot."""
        with SessionLocal() as db:
            study = db.get(MSStudy, study_id)
            protocol_id = study.protocol_id if study is not None else PROTOCOL_ID
            call = claim_next_call(
                db,
                study_id,
                worker_id=worker_id,
                slot_id=slot_id,
                model=model,
                condition=condition,
            )
            # Reuse the same physical memory-condition slot for the
            # optional no-feedback wave once the full wave has drained.
            if call is None and condition in set(_full_wave_conditions(protocol_id)) - {
                "no_memory"
            }:
                call = claim_next_call(
                    db,
                    study_id,
                    worker_id=worker_id,
                    slot_id=slot_id,
                    model=model,
                    condition=_paired_feedback_condition(condition),
                )
        if call is None:
            with SessionLocal() as db:
                study = db.get(MSStudy, study_id)
                if study is None or study.status != StudyStatus.running:
                    return True
                # Stay alive while any runnable shard still owns pending
                # work for this slot's model/condition.  Gating on the
                # shard table (not a head window of pending rows) keeps a
                # late-launched shard from being mistaken for an empty
                # queue just because its calls sit deep in the id order.
                # A study without shard rows at all is legacy and keeps
                # the old unconditional wait.
                shard_count = db.scalar(
                    select(func.count(MSQuestionRun.id)).where(
                        MSQuestionRun.study_id == study_id
                    )
                )
                runnable_filters = []
                if shard_count:
                    runnable_question_ids = list(
                        db.scalars(
                            select(MSQuestionRun.question_id).where(
                                MSQuestionRun.study_id == study_id,
                                MSQuestionRun.status == "running",
                            )
                        )
                    )
                    if not runnable_question_ids:
                        return True
                    runnable_filters.append(
                        MSCall.question_id.in_(runnable_question_ids)
                    )
                pending_exists = db.scalar(
                    select(MSCall.id)
                    .where(
                        MSCall.study_id == study_id,
                        MSCall.model == model,
                        *(
                            [
                                MSCall.condition.in_(
                                        {
                                            condition,
                                            (
                                                _paired_feedback_condition(condition)
                                                if condition in set(
                                                    _full_wave_conditions(study.protocol_id)
                                                )
                                                else condition
                                            ),
                                        }
                                )
                            ]
                            if condition
                            else []
                        ),
                        MSCall.status == CallStatus.pending,
                        *runnable_filters,
                    )
                    .limit(1)
                )
                if pending_exists is None:
                    return True
            stop_event.wait(0.5)
            return False
        if stop_event.is_set():
            self._requeue_after_stop(call.id)
            return True
        self._set_slot_runtime(
            worker_id,
            slot_id,
            state="running",
            model=model,
            call_id=call.id,
            kind=call.kind,
            phase="training" if call.kind == CallKind.memory_write else "scoring",
            attempt=call.attempt_count + 1,
        )
        outcome = self._execute_call_strict(
            study_id,
            call.id,
            model=call.model,
            worker_id=worker_id,
            slot_id=slot_id,
            runner=runner,
            stop_event=stop_event,
        )
        if outcome is _CallOutcome.DEFERRED:
            # 退避重试的调用已回队（带 retry_after），槽立即恢复 idle 继续
            # 服务其他分片；这不是分片级失败。
            self._set_slot_runtime(worker_id, slot_id, state="idle", model=model)
            return False
        if not outcome:
            with SessionLocal() as db:
                study = db.get(MSStudy, study_id)
                stopped = study is None or study.status in {
                    StudyStatus.paused,
                    StudyStatus.terminated,
                }
            self._set_slot_runtime(
                worker_id,
                slot_id,
                state="stopped" if stopped else "failed",
                model=model,
                call_id=call.id,
            )
            if stop_event.is_set() or stopped:
                # The stop signal may race with the final claim check.  A
                # call that was leased but never entered execution must be
                # returned to the queue rather than left for lease expiry.
                self._requeue_after_stop(call.id)
                return True
            # A terminal call failure pauses only its question shard.  A
            # fixed slot may continue serving other runnable questions or
            # streams without bypassing the failed predecessor.
            return False
        self._set_slot_runtime(worker_id, slot_id, state="idle", model=model)
        return False

    def _execute_call_strict(
        self,
        study_id: str,
        call_id: int,
        *,
        model: str,
        worker_id: str,
        slot_id: str,
        runner: Any,
        stop_event: Event | None = None,
    ) -> _CallOutcome:
        """Run one logical call with bounded retries for transient failures.

        Returns SUCCEEDED, FAILED (terminal, shard-pausing), or DEFERRED
        (transient failure requeued with exponential backoff).
        """
        with SessionLocal() as db:
            study = db.get(MSStudy, study_id)
            if study is None or study.status != StudyStatus.running:
                return _CallOutcome.FAILED
        for execution_number in range(MAX_AUTOMATIC_ATTEMPTS):
            # Pause/terminate and a peer's terminal failure can race with the
            # lease claim.  Check the stop signal immediately before starting
            # another subprocess so a claimed-but-not-started call is returned
            # to the queue instead of issuing work after the safe-stop point.
            if stop_event is not None and stop_event.is_set():
                self._requeue_after_stop(call_id)
                return _CallOutcome.FAILED
            outcome = self._execute_call(
                study_id,
                call_id,
                model=model,
                worker_id=worker_id,
                slot_id=slot_id,
                runner=runner,
            )
            if outcome is _CallOutcome.SUCCEEDED:
                return _CallOutcome.SUCCEEDED
            with SessionLocal() as db:
                call = db.get(MSCall, call_id)
                code = call.failure_code if call is not None else None
            if code in PENDING_NON_RETRYABLE_CODES:
                # 数据/配置级错误：重试无意义，立即终态化并暂停分片。
                self._mark_call_failed(call_id)
                return _CallOutcome.FAILED
            if not _is_retryable_code(code):
                # 未知/输出类错误保持原有语义：单次终态，人工介入。
                self._mark_call_failed(call_id)
                return _CallOutcome.FAILED
            if execution_number >= MAX_AUTOMATIC_ATTEMPTS - 1:
                # 基础设施类瞬态失败：转为指数退避重新排队，而不是终态
                # 失败。等待期间槽服务其他分片；到期的调用由领取查询
                # 自然重新拾起，attempt 账本完整保留。
                failures = int(call.attempt_count) if call is not None else 1
                delay = _next_backoff_seconds(failures)
                with SessionLocal() as db2:
                    deferred = db2.get(MSCall, call_id)
                    if deferred is not None and deferred.status in {
                        CallStatus.running,
                        CallStatus.leased,
                    }:
                        _defer_call_for_backoff(
                            db2,
                            deferred,
                            retry_after=utc_now_naive() + timedelta(seconds=delay),
                        )
                        db2.commit()
                self._set_slot_runtime(
                    worker_id,
                    slot_id,
                    state="backoff",
                    model=model,
                    call_id=call_id,
                    retry_after=(utc_now_naive() + timedelta(seconds=delay)).isoformat()
                    + "Z",
                )
                return _CallOutcome.DEFERRED
            # A pause, peer-slot failure, or termination must not leave a
            # leased row stranded.  Keep the failed attempt in the ledger and
            # put the logical call back at the head of its model queue.
            if stop_event is not None and stop_event.is_set():
                self._requeue_after_stop(call_id)
                return _CallOutcome.FAILED
            self._prepare_call_retry(call_id, worker_id, slot_id)
            delay = RETRY_BACKOFF_SECONDS[execution_number] + random.uniform(
                0.0, RETRY_JITTER_SECONDS
            )
            self._set_slot_runtime(
                worker_id,
                slot_id,
                state="backoff",
                model=model,
                call_id=call_id,
                # ``utc_now_naive`` matches the database's UTC timestamp type;
                # add the explicit designator for browser countdown parsing.
                retry_after=(utc_now_naive() + timedelta(seconds=delay)).isoformat()
                + "Z",
                attempt=execution_number + 2,
            )
            if stop_event is not None:
                if stop_event.wait(delay):
                    self._requeue_after_stop(call_id)
                    return _CallOutcome.FAILED
            else:
                time.sleep(delay)
        return _CallOutcome.FAILED

    @staticmethod
    def _requeue_after_stop(call_id: int) -> None:
        with SessionLocal() as db:
            call = db.get(MSCall, call_id)
            if call is not None and call.status in {
                CallStatus.running,
                CallStatus.leased,
            }:
                call.status = CallStatus.pending
                call.worker_id = None
                call.slot_id = None
                call.lease_until = None
                call.retry_after = None
                call.failure_code = None
                call.failure_summary = None
                db.commit()

    @staticmethod
    def _prepare_call_retry(call_id: int, worker_id: str, slot_id: str) -> None:
        with SessionLocal() as db:
            call = db.get(MSCall, call_id)
            if call is None or call.status not in {
                CallStatus.running,
                CallStatus.leased,
            }:
                return
            call.status = CallStatus.leased
            call.worker_id = worker_id
            call.slot_id = slot_id
            call.lease_until = utc_now_naive() + timedelta(seconds=LEASE_SECONDS)
            call.started_at = utc_now_naive()
            call.failure_code = None
            call.failure_summary = None
            call.completed_at = None
            db.commit()

    def _mark_call_failed(self, call_id: int) -> None:
        """Persist a terminal failure and pause the study at that call."""
        with SessionLocal() as db:
            call = db.get(MSCall, call_id)
            if call is None:
                return
            if call.status in {
                CallStatus.pending,
                CallStatus.leased,
                CallStatus.running,
            }:
                call.status = CallStatus.failed
                call.lease_until = None
                call.retry_after = None
                call.completed_at = utc_now_naive()
                if call.memory_store_id and call.kind == CallKind.memory_write:
                    store = db.get(MSMemoryStore, call.memory_store_id)
                    if store is not None:
                        # Derive the verdict from the ledger rather than forcing
                        # it: the recompute agrees this store is failed (it has
                        # a failed write and not every write succeeded) and
                        # keeps committed_count truthful.  Autoflush is off, so
                        # the status set above has to be flushed first.
                        db.flush()
                        recompute_store_progress(db, store)
                        store.error_summary = call.failure_summary
                study = db.get(MSStudy, call.study_id)
                if study is not None:
                    shard = db.scalar(
                        select(MSQuestionRun).where(
                            MSQuestionRun.study_id == call.study_id,
                            MSQuestionRun.question_id == call.question_id,
                        )
                    )
                    if shard is not None:
                        shard.status = "attention_required"
                        shard.error_summary = (
                            f"call {call.id} failed; manual retry required"
                        )
                    elif study.status == StudyStatus.running:
                        # Legacy rows without a shard table retain the old
                        # study-wide stop behavior.
                        study.status = StudyStatus.attention_required
                        study.error_summary = (
                            f"call {call.id} failed; manual retry required"
                        )
                db.commit()

    def _execute_call(
        self,
        study_id: str,
        call_id: int,
        *,
        model: str,
        worker_id: str,
        slot_id: str,
        runner: Any,
    ) -> "_CallOutcome":
        with SessionLocal() as db:
            call = db.scalar(
                select(MSCall).where(MSCall.id == call_id, MSCall.study_id == study_id)
            )
            study = db.get(MSStudy, study_id)
            if call is None or study is None:
                return _CallOutcome.SUCCEEDED
            if call.status == CallStatus.cancelled:
                return _CallOutcome.SUCCEEDED
            if call.status in {
                CallStatus.pending,
                CallStatus.leased,
                CallStatus.running,
            }:
                call.status = CallStatus.running
                # One attempt row per execution; the number must be unique per
                # call or `uq_ms_call_attempt` rejects the row.  Claiming does
                # not increment: every retry is a fresh execution.
                call.attempt_count += 1
                db.commit()
            started = time.monotonic()
            try:
                if runner is None:
                    raise RunnerUnavailableError("codex runner is not available")
                if call.kind == CallKind.memory_write:
                    self._execute_memory_write(db, study, call, runner, model)
                else:
                    self._execute_score(db, study, call, runner, model)
                # Termination can cancel a row while the child process is
                # finishing.  Do not resurrect that logical call as a
                # success after the API has made the cancellation durable.
                current_status = db.scalar(
                    select(MSCall.status).where(MSCall.id == call.id)
                )
                if current_status == CallStatus.cancelled:
                    # ``terminate`` may cancel the logical row while the
                    # child happens to return a value.  Preserve an explicit
                    # interrupted attempt instead of silently dropping the
                    # in-flight evidence (and refresh the identity-mapped row
                    # before committing so we do not resurrect ``running``).
                    db.refresh(call)
                    interrupted = [
                        value
                        for value in db.new
                        if isinstance(value, MSCallAttempt)
                        and value.call_id == call.id
                        and value.attempt_number == call.attempt_count
                    ]
                    if not interrupted:
                        interrupted = list(
                            db.scalars(
                                select(MSCallAttempt).where(
                                    MSCallAttempt.call_id == call.id,
                                    MSCallAttempt.attempt_number == call.attempt_count,
                                )
                            )
                        )
                    if interrupted:
                        attempt = interrupted[0]
                        attempt.status = "failed"
                        attempt.error_type = "interrupted"
                        attempt.error_message = (
                            "call was cancelled while its Codex execution was in flight"
                        )
                    else:
                        db.add(
                            MSCallAttempt(
                                call_id=call.id,
                                attempt_number=call.attempt_count,
                                request_sha256=_call_request_sha(call),
                                status="failed",
                                requested_model=model,
                                input_tokens=call.input_tokens,
                                output_tokens=call.output_tokens,
                                total_tokens=call.total_tokens,
                                latency_ms=int((time.monotonic() - started) * 1000),
                                error_type="interrupted",
                                error_message=(
                                    "call was cancelled while its Codex execution "
                                    "was in flight"
                                ),
                                started_at=call.started_at or utc_now_naive(),
                            )
                        )
                    db.commit()
                    return _CallOutcome.SUCCEEDED
                call.status = CallStatus.succeeded
                call.completed_at = utc_now_naive()
                call.latency_ms = int((time.monotonic() - started) * 1000)
                call.lease_until = None
                call.retry_after = None
                call.failure_code = None
                call.failure_summary = None
                # Memory writes append their success attempt only after the
                # filesystem publish returns.  Fill its measured duration
                # here, once the outer logical call has a definitive latency,
                # so successful and failed attempts are equally complete.
                for pending_attempt in db.new:
                    if (
                        isinstance(pending_attempt, MSCallAttempt)
                        and pending_attempt.call_id == call.id
                        and pending_attempt.attempt_number == call.attempt_count
                        and pending_attempt.status == "succeeded"
                    ):
                        pending_attempt.latency_ms = call.latency_ms
                        pending_attempt.started_at = (
                            call.started_at or pending_attempt.started_at
                        )
                if call.kind == CallKind.memory_write and call.memory_store_id:
                    store = db.get(MSMemoryStore, call.memory_store_id)
                    if store is not None:
                        # ``SessionLocal`` runs with autoflush disabled, so the
                        # row-level status above must be flushed before the
                        # ledger count inside the recompute can see it.
                        db.flush()
                        recompute_store_progress(db, store)
                db.commit()
                return _CallOutcome.SUCCEEDED
            except Exception as exc:
                # The attempt row is written here; the terminal call failure is
                # recorded by _execute_call_strict, which then pauses the study.
                #
                # The rollback is deliberately conditional.  A blanket rollback
                # also discarded the framework invocation ledger that the bridge
                # had already flushed, and that ledger is the only record of
                # *why* an internal request failed: it feeds the audit check
                # `no_failed_framework_invocations` and the progress counters.
                # Losing it meant a run whose every memory write had failed
                # still showed an empty, blameless invocation table.  An
                # application-level transport error leaves the session usable,
                # so the ledger survives and is committed below together with
                # the attempt row.  A database-level failure is the case that
                # genuinely needs the rollback, and SQLAlchemy reports it by
                # taking the session out of its active state.
                if not db.is_active:
                    db.rollback()
                call = db.scalar(select(MSCall).where(MSCall.id == call_id))
                if call is None:
                    return _CallOutcome.SUCCEEDED
                code = _failure_code(exc)
                summary = _redact_text(exc, 2_000)
                interrupted_attempt = (
                    call.status == CallStatus.cancelled
                    and isinstance(exc, RunnerInterruptedError)
                )
                # A concurrent reclaim may have already recorded this same
                # (call_id, attempt_number) as lease_expired and requeued the
                # call.  Never write a duplicate attempt row here.
                attempt_exists = db.scalar(
                    select(func.count(MSCallAttempt.id)).where(
                        MSCallAttempt.call_id == call.id,
                        MSCallAttempt.attempt_number == call.attempt_count,
                    )
                )
                if attempt_exists:
                    # Known residual gap: this rollback also discards the
                    # invocation ledger for an attempt that a concurrent
                    # reclaim has already requeued.  Keeping it would mean
                    # committing half-finished work for a call that is about to
                    # be retried elsewhere, so the evidence is dropped instead.
                    # Unlike the old blanket rollback this only affects the
                    # lease-expiry race, not the ordinary failure path.
                    db.rollback()
                    return _CallOutcome.FAILED
                invocation_rows = list(
                    db.scalars(
                        select(MSFrameworkInvocation).where(
                            MSFrameworkInvocation.call_id == call.id,
                            MSFrameworkInvocation.attempt_number == call.attempt_count,
                        )
                    )
                )
                if invocation_rows:
                    call.input_tokens = (
                        _sum_optional(
                            invocation.input_tokens for invocation in invocation_rows
                        )
                        or call.input_tokens
                    )
                    call.output_tokens = _sum_optional(
                        invocation.output_tokens for invocation in invocation_rows
                    )
                    call.total_tokens = _sum_optional(
                        invocation.total_tokens for invocation in invocation_rows
                    )
                db.add(
                    MSCallAttempt(
                        call_id=call.id,
                        attempt_number=call.attempt_count,
                        request_sha256=_call_request_sha(call),
                        status="failed",
                        requested_model=model,
                        input_tokens=call.input_tokens,
                        output_tokens=call.output_tokens,
                        total_tokens=call.total_tokens,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        stderr_excerpt=_redact_text(
                            getattr(exc, "stderr", "") or summary, 800
                        ),
                        error_type="interrupted" if interrupted_attempt else code,
                        error_message=summary,
                        started_at=call.started_at or utc_now_naive(),
                    )
                )
                call.failure_code = code
                call.failure_summary = summary
                call.latency_ms = int((time.monotonic() - started) * 1000)
                db.commit()
                return _CallOutcome.FAILED

    def _execute_memory_write(
        self, db: Session, study: MSStudy, call: MSCall, runner: Any, model: str
    ) -> None:
        store = db.get(MSMemoryStore, call.memory_store_id)
        if store is None:
            raise RuntimeError("memory write references missing store")
        with _isolated_store_attempt(store):
            self._execute_memory_write_body(db, study, call, runner, model)
        # Path relocation during publication can change the canonical snapshot
        # hash; keep the logical call's memory reference aligned with the
        # committed DB/file payload.
        call.memory_hash = store.snapshot_sha256
        # The filesystem promotion above is the commit point.  Only now is it
        # safe to append the logical success attempt; a promotion failure must
        # become a failed attempt with the same number, never a duplicate pair
        # of success/failure rows.
        db.add(
            _attempt(
                call,
                model,
                call.request_json or {"call_id": call.id},
                None,
                {
                    "request_tokens": call.input_tokens,
                    "stored_tokens": call.output_tokens,
                },
            )
        )

    def _execute_memory_write_body(
        self, db: Session, study: MSStudy, call: MSCall, runner: Any, model: str
    ) -> None:
        from app.models.memory_study import MSRecord

        store = db.get(MSMemoryStore, call.memory_store_id)
        row = db.scalar(
            select(MSRecord).where(
                MSRecord.study_id == study.id, MSRecord.answer_id == call.answer_id
            )
        )
        if store is None or row is None:
            raise RuntimeError("memory write references missing store or record")
        _prepare_empty_store_for_write(store)
        if not _snapshot_is_valid(store):
            raise MemoryFrameworkError(
                "memory store snapshot validation failed before writing"
            )
        if store.framework not in {"retrieval", "mem0", "amem"}:
            raise RuntimeError("memory write references an unsupported framework")
        call.request_json = {
            "record_id": row.answer_id,
            "framework": store.framework,
            "feedback_mode": store.feedback_mode,
        }
        call.input_tokens = payload_token_counts(call.request_json)["request_tokens"]
        runtime = _runtime_for_model(db, study, model)
        _assert_runner_runtime(runner, runtime)
        recorder = (
            FrameworkInvocationRecorder(db=db, call=call, framework=store.framework)
            if store.framework in {"mem0", "amem"}
            else None
        )
        adapter = _adapter(
            db,
            study,
            store,
            runner=runner,
            runtime=runtime,
            recorder=recorder,
        )
        record = _record(db, study.id, row.answer_id)
        # The official framework owns extraction and evolution; every internal
        # request is issued by the attached Codex bridge and logged separately.
        ingest_result = adapter.ingest(type("StudyRecordProxy", (), record)())
        call.output_json = {"framework_result": ingest_result}
        invocation_rows = list(
            db.scalars(
                select(MSFrameworkInvocation).where(
                    MSFrameworkInvocation.call_id == call.id,
                    MSFrameworkInvocation.attempt_number == call.attempt_count,
                )
            )
        )
        call.input_tokens = _sum_optional(
            invocation.input_tokens for invocation in invocation_rows
        )
        call.output_tokens = _sum_optional(
            invocation.output_tokens for invocation in invocation_rows
        )
        call.total_tokens = _sum_optional(
            invocation.total_tokens for invocation in invocation_rows
        )
        call.memory_hash = (
            memory_hash(store.snapshot_json or {}) if store.snapshot_json else None
        )
        snapshot = adapter.snapshot()
        if not store.snapshot_path:
            raise RuntimeError("memory store has no snapshot path")
        _atomic_snapshot(store.snapshot_path, snapshot)
        store.snapshot_json = snapshot
        store.snapshot_sha256 = memory_hash(snapshot)
        store.snapshot_artifact_sha256 = snapshot.get("artifact_sha256")
        store.framework_version = snapshot.get("framework_version")
        store.snapshot_stream_id = snapshot.get("stream_id") or _stream_id(store)
        store.last_call_id = call.id
        # The store verdict is recomputed by ``_execute_call`` once this call is
        # recorded as succeeded -- recomputing here would undercount it, since
        # the in-memory status is still ``running`` at this point.
        call.memory_hash = store.snapshot_sha256

    def _execute_score(
        self, db: Session, study: MSStudy, call: MSCall, runner: Any, model: str
    ) -> None:
        from app.models.memory_study import MSRecord

        row = db.scalar(
            select(MSRecord).where(
                MSRecord.study_id == study.id, MSRecord.answer_id == call.answer_id
            )
        )
        if row is None:
            raise RuntimeError("score call references missing record")
        retrieval: list[dict[str, Any]] = []
        if call.condition != "no_memory":
            store = db.get(MSMemoryStore, call.memory_store_id)
            if store is None:
                raise RuntimeError("score call references missing memory store")
            # Training scores read the last committed prefix.  Test scores
            # are claimed only after the model/question store barrier, but the
            # check remains here as a second defense against a stale worker.
            if row.selection_kind == "test" and store.status != "completed":
                raise RuntimeError(
                    "score attempted before final memory snapshot was committed"
                )
            if not _snapshot_is_valid(store):
                raise MemoryFrameworkError(
                    "memory store snapshot validation failed before scoring"
                )
            with _isolated_store_read(store):
                adapter = _adapter(db, study, store)
                retrieval_top_k = study.config_json.get("retrieval_top_k")
                if not isinstance(retrieval_top_k, int) or isinstance(
                    retrieval_top_k, bool
                ) or retrieval_top_k <= 0:
                    raise MemoryFrameworkError(
                        "frozen study config has invalid retrieval_top_k"
                    )
                retrieved = adapter.retrieve(
                    type("StudyRecordProxy", (), _record(db, study.id, row.answer_id))(),
                    top_k=retrieval_top_k,
                )
            audit_retrieval = [
                {
                    "rank": rank,
                    "answer_id": item.answer_id,
                    "similarity": item.similarity,
                    "native_relevance_score": item.native_relevance_score,
                    "score": item.score,
                    "payload": item.payload,
                }
                for rank, item in enumerate(retrieved, start=1)
            ]
            if study.protocol_id == V3_R2_PROTOCOL_ID:
                retrieval = [
                    _minimal_memory_case(item, framework=store.framework)
                    for item in retrieved
                ]
            else:
                retrieval = [
                    {
                        "answer_id": item.answer_id,
                        "similarity": item.similarity,
                        "score": item.score,
                        "payload": item.payload,
                    }
                    for item in retrieved
                ]
            call.memory_hash = store.snapshot_sha256
        else:
            audit_retrieval = retrieval
        payload = _record(db, study.id, row.answer_id)
        messages = _score_messages(call, payload, retrieval)
        # Persist a redacted request envelope before the child process starts.
        # If the runner fails, the failure attempt still has the exact request
        # hash and token count rather than the old ``{"call_id": ...}``
        # placeholder.
        request_payload = {"messages": list(messages), "schema": "ScoreOutput"}
        # Keep the full prompt in process memory only.  The durable call row
        # carries its hash and token count, while the attempt ledger remains
        # independently auditable without retaining student/reference text.
        call.request_json = {
            "request_sha256": _request_sha256(request_payload),
            "schema": "ScoreOutput",
            "message_count": len(messages),
            "protocol_id": study.protocol_id,
        }
        counts = payload_token_counts(messages[1]["content"])
        call.input_tokens = counts["request_tokens"]
        runtime = _runtime_for_model(db, study, model)
        _assert_runner_runtime(runner, runtime)
        result = runner.run(
            messages=messages,
            schema=ScoreOutput,
            runtime=runtime,
        )
        value = result.value
        score = float(value["score"])
        if not score == score or score in {float("inf"), float("-inf")}:
            raise ValueError("model score must be finite")
        minimum = _question_score_floor(study, row.question_id)
        maximum = _question_score_ceiling(study, row.question_id)
        if study.protocol_id == V3_R2_PROTOCOL_ID and score < minimum:
            raise ValueError("model score is below the question score floor")
        if score > maximum:
            raise ValueError("model score exceeds the question score ceiling")
        call.retrieval_json = audit_retrieval
        call.output_json = value
        call.manual_score = row.teacher_score
        call.model_score = score
        call.max_score = maximum
        call.signed_diff = score - row.teacher_score
        call.absolute_diff = abs(call.signed_diff)
        call.normalized_signed_diff = call.signed_diff / maximum
        call.normalized_absolute_diff = call.absolute_diff / maximum
        call.output_tokens = payload_token_counts(value)["stored_tokens"]
        call.total_tokens = (call.input_tokens or 0) + (call.output_tokens or 0)
        db.add(_attempt(call, model, request_payload, result, counts))

    def _acquire_runtime(
        self,
        worker_id: str,
        study_id: str,
        *,
        slot_models: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        slot_models = slot_models or slot_specs()
        with SessionLocal() as db:
            now = utc_now_naive()
            row = db.scalar(
                select(MSSchedulerRuntime)
                .where(MSSchedulerRuntime.id == 1)
                .with_for_update()
            )
            if row is None:
                row = MSSchedulerRuntime(id=1)
                db.add(row)
                db.flush()
            if (
                row.lease_until
                and row.lease_until >= now
                and row.owner_id not in {None, worker_id}
            ):
                if _worker_alive(row.worker_pid, row.worker_host):
                    raise RuntimeError(
                        "another memory-study worker owns the execution slots"
                    )
                # 持有租约的进程已死（crash/被杀时来不及 release）：立即强制
                # 接管，而不是干等 600s 租约自然过期——那会让 UI 在最长
                # 10 分钟内一直显示 "not connected" 且实验停摆。
                logger.warning(
                    "force-recovering lease from dead worker pid=%s owner=%s",
                    row.worker_pid,
                    row.owner_id,
                )
            row.owner_id = worker_id
            row.status = "online"
            row.max_subprocesses = MAX_CODEX_SUBPROCESSES
            row.active_subprocesses = 0
            row.worker_pid = os.getpid()
            row.worker_host = socket.gethostname()
            row.slots_json = {
                slot_id: {
                    "model": model[0] if isinstance(model, tuple) else model,
                    "condition": model[1] if isinstance(model, tuple) else None,
                    "study_id": study_id,
                    "state": "starting",
                }
                for slot_id, model in slot_models.items()
            }
            row.heartbeat_at = now
            row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            db.commit()

    @staticmethod
    def _set_slot_runtime(worker_id: str, slot_id: str, **updates: Any) -> None:
        """Publish bounded per-slot state for the monitor without secrets."""
        with SessionLocal() as db:
            row = db.get(MSSchedulerRuntime, 1)
            if row is None or row.owner_id != worker_id:
                return
            slots = dict(row.slots_json or {})
            slot = dict(slots.get(slot_id) or {})
            slot.update(
                {key: value for key, value in updates.items() if value is not None}
            )
            slot["heartbeat_at"] = utc_now_naive().isoformat()
            if updates.get("state") != "backoff":
                slot.pop("retry_after", None)
            if updates.get("state") == "idle":
                for key in ("call_id", "kind", "attempt", "phase"):
                    slot.pop(key, None)
            slots[slot_id] = slot
            row.slots_json = slots
            row.active_subprocesses = sum(
                1 for item in slots.values() if item.get("state") == "running"
            )
            db.commit()

    @staticmethod
    def _touch_runtime(worker_id: str) -> None:
        with SessionLocal() as db:
            row = db.get(MSSchedulerRuntime, 1)
            if row is not None and row.owner_id == worker_id:
                now = utc_now_naive()
                row.heartbeat_at = now
                row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
                # 防御性同步：保证 PID/host 始终与当前 owner 一致（例如接管了
                # 没有身份字段的旧残留租约后，首次续租即补全身份）。
                row.worker_pid = os.getpid()
                row.worker_host = socket.gethostname()
                slots = dict(row.slots_json or {})
                for slot_id, slot_data in slots.items():
                    slot = dict(slot_data or {})
                    slot["heartbeat_at"] = now.isoformat()
                    slots[slot_id] = slot
                row.slots_json = slots
                db.commit()

    @staticmethod
    def _touch_running_call_leases(worker_id: str) -> None:
        """Extend per-call leases of this worker's in-flight calls.

        Reclaim logic treats a live heartbeat as proof the slot is still
        executing, but only for the runtime lease; the call-level lease also
        had to outlast the slowest framework invocation or another process's
        sweep would requeue a call mid-flight.  Renewing both on the same
        heartbeat keeps the two leases consistent.
        """
        with SessionLocal() as db:
            calls = list(
                db.scalars(
                    select(MSCall).where(
                        MSCall.worker_id == worker_id,
                        MSCall.status.in_([CallStatus.leased, CallStatus.running]),
                        MSCall.lease_until.is_not(None),
                    )
                )
            )
            if not calls:
                db.rollback()
                return
            new_lease = utc_now_naive() + timedelta(seconds=LEASE_SECONDS)
            for call in calls:
                call.lease_until = new_lease
            db.commit()

    def _release_runtime(self, worker_id: str) -> None:
        with SessionLocal() as db:
            row = db.get(MSSchedulerRuntime, 1)
            if row and row.owner_id == worker_id:
                row.status = "offline"
                row.active_subprocesses = 0
                row.slots_json = {}
                row.lease_until = None
                row.worker_pid = None
                row.worker_host = None
                row.heartbeat_at = utc_now_naive()
                db.commit()


def _store_expected_count(db: Session, store: MSMemoryStore) -> int:
    return int(
        db.scalar(
            select(func.count(MSCall.id)).where(
                MSCall.memory_store_id == store.id, MSCall.kind == CallKind.memory_write
            )
        )
        or 0
    )


def recompute_store_progress(db: Session, store: MSMemoryStore) -> None:
    """Derive ``committed_count``/``history_count``/``status`` from the ledger.

    Every value is a pure function of the store's own ``memory_write`` calls, so
    the store can never disagree with its calls no matter how many times a run
    is retried or interrupted:

    ====================  ============================================
    ledger state          store status
    ====================  ============================================
    all writes succeeded  ``completed``
    no write succeeded    ``pending`` (nothing was committed yet)
    some writes failed    ``failed``    (an unretried terminal failure)
    otherwise             ``pending``   (training still in progress)
    ====================  ============================================

    Why this replaced the old ``committed_count += 1`` counter: the manual
    retry endpoints requeue every failed write of a store, so after a partial
    commit a single replayed write used to push the counter past the expected
    total and mark the store ``completed`` while most of its training data was
    still missing -- scoring would then run against a half-built memory.

    A valid snapshot recovery requeues only failed writes, while a snapshot
    that cannot be proven is cleared and rebuilt from scratch.  Both paths
    therefore avoid layering a replay onto an unknown native state; the
    surviving successful ledger rows remain the source of truth.
    """
    expected = _store_expected_count(db, store)
    counts = dict(
        db.execute(
            select(MSCall.status, func.count(MSCall.id))
            .where(
                MSCall.memory_store_id == store.id,
                MSCall.kind == CallKind.memory_write,
            )
            .group_by(MSCall.status)
        ).all()
    )
    succeeded = int(counts.get(CallStatus.succeeded, 0))
    failed = int(counts.get(CallStatus.failed, 0))
    store.committed_count = succeeded
    store.history_count = succeeded
    if not expected:
        # Deterministic retrieval stores own no write calls at all; they are
        # materialized complete when the project is created.  Deriving them
        # from an empty ledger would flip them to ``pending`` and close the
        # scoring barrier forever.
        store.status = "completed"
    elif succeeded >= expected:
        store.status = "completed"
    elif failed:
        store.status = "failed"
    else:
        store.status = "pending"


def _sum_optional(values: Any) -> int | None:
    numbers = [int(value) for value in values if value is not None]
    return sum(numbers) if numbers else None


def _attempt(
    call: MSCall, model: str, request: dict, result: Any, counts: dict[str, int]
) -> MSCallAttempt:
    import hashlib

    return MSCallAttempt(
        call_id=call.id,
        attempt_number=call.attempt_count,
        request_sha256=hashlib.sha256(_json(request).encode()).hexdigest(),
        status="succeeded",
        requested_model=model,
        input_tokens=counts.get("request_tokens"),
        output_tokens=counts.get("stored_tokens"),
        total_tokens=_sum_optional(
            (counts.get("request_tokens"), counts.get("stored_tokens"))
        ),
        latency_ms=getattr(result, "latency_ms", None),
        stderr_excerpt=(
            _redact_text(getattr(result, "stderr_excerpt", ""), 800)
            if getattr(result, "stderr_excerpt", "")
            else None
        ),
        started_at=call.started_at or utc_now_naive(),
    )


def _failure_code(exc: Exception) -> str:
    message = str(exc).lower()
    # A projection failure is typed, so it is decided before any keyword scan.
    # Its message quotes the offending stored memory, so scanning it could
    # otherwise report an auth or transient runner error whenever the quoted
    # prose happens to contain "401" or "timed out".
    if isinstance(exc, MemoryEvidenceError):
        return "memory_evidence_invalid"
    # Runner transport/time-limit failures must be classified before scanning
    # stderr for words such as "schema", "provider", or "sandbox".  The
    # Codex diagnostic envelope contains those words even when no schema or
    # authentication check failed.
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        marker in message
        for marker in (
            "timed out after",
            "timeout after",
            "process timed out",
            "connection reset",
            "connection aborted",
            "broken pipe",
        )
    ):
        return "runner_transient"
    if any(
        marker in message
        for marker in (
            "auth",
            "login",
            "logged in",
            "unauthor",
            "credentials",
            "permission denied",
            "forbidden",
            "401",
            "403",
        )
    ):
        return "authentication_error"
    if "exited with" in message or "exit code" in message:
        # A nonzero CLI exit is a runner-level crash (stream error, usage
        # limit, OOM kill), not a data problem -- and the diagnostic envelope
        # embeds the prompt, which itself contains words such as "schema",
        # so this must be decided before the keyword scans below.
        return "runner_transient"
    if "schema" in message:
        return "schema_error"
    if "snapshot" in message or "artifact hash" in message:
        return "snapshot_validation_error"
    if any(
        marker in message for marker in ("dependency", "package", "revision mismatch")
    ):
        return "dependency_error"
    if "model" in message and any(
        marker in message for marker in ("invalid", "unknown", "unsupported", "config")
    ):
        return "model_configuration_error"
    if isinstance(exc, RunnerDriftError):
        return "runtime_drift_error"
    if isinstance(exc, RunnerInterruptedError):
        return "runner_interrupted"
    if isinstance(exc, MemoryFrameworkError):
        return (
            "framework_transient"
            if _transient_exception(exc)
            else "framework_unavailable"
        )
    if isinstance(exc, RunnerUnavailableError):
        return "runner_unavailable"
    if isinstance(exc, RunnerExecutionError):
        if _transient_exception(exc):
            return "runner_transient"
        return (
            "output_validation_error"
            if "structured result" in message or "output" in message
            else "runner_execution_error"
        )
    if "structured result" in message or "output" in message:
        return "output_validation_error"
    if _transient_exception(exc):
        return "runner_transient"
    return "invalid_output_or_runtime_error"


def _is_transient_message(message: str) -> bool:
    return bool(re.search(r"\b5\d{2}\b", message)) or any(
        marker in message
        for marker in (
            "429",
            "rate_limit",
            "rate limit",
            "too many requests",
            "temporarily unavailable",
            "service unavailable",
            "connection reset",
            "connection refused",
            "connection aborted",
            "timed out",
            "timeout",
            "502",
            "503",
            "504",
            "bad gateway",
            "gateway timeout",
            "internal server error",
            "server error",
            "network error",
            "network",
            "unreachable",
            "reset by peer",
            "connection lost",
            # SQLite can report this when an active Mem0/Chroma client loses
            # the attempt directory while a worker recovery/cleanup races it.
            # Retry on a fresh isolated attempt after the filesystem state is
            # restored; persistent permission errors still exhaust the bound.
            "readonly database",
            "dns",
            "broken pipe",
            "eof",
        )
    )


def _transient_exception(exc: Exception) -> bool:
    """Inspect common HTTP-client status attributes without adding a dependency."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if status in {408, 429} or 500 <= status <= 599:
            return True
    return _is_transient_message(str(exc).lower())


def _is_retryable_code(code: str | None) -> bool:
    return code in {"runner_transient", "framework_transient", "runner_interrupted"}


__all__ = ["MemoryStudyWorker", "claim_next_call"]
