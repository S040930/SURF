"""Persistent worker for r20 streams."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.executor import (
    AUTO_RETRY_SECONDS,
    complete_stream,
    execute_call,
    has_retry_pending,
    has_unrecorded,
    next_call,
    next_retry,
)
from app.experiment.r20.prompt_validation import process_validation_call
from app.models.r20 import (
    R20Call,
    R20Project,
    R20PromptValidationSuite,
    R20RunGroup,
    R20Stream,
)

CONCURRENCY = int(os.getenv("R20_WORKER_CONCURRENCY", "3"))
POLL_SECONDS = float(os.getenv("R20_WORKER_POLL_SECONDS", "2"))
LEASE_SECONDS = int(os.getenv("R20_STREAM_LEASE_SECONDS", "600"))
MIN_REQUEST_INTERVAL = float(os.getenv("R20_MIN_REQUEST_INTERVAL_SECONDS", "1.0"))
logger = logging.getLogger(__name__)

_PACE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def _pace_request() -> None:
    """Serialize provider request starts across worker threads.

    Concurrent loops firing bursts at the same provider account trip Ark's
    account rate limit (429 AccountRateLimitExceeded) and queue hangs. A short
    minimum interval between request starts keeps traffic steady; combined with
    the retry gate (retries execute before any new work) this keeps provider
    pressure low and smooth.
    """
    global _LAST_REQUEST_AT
    if MIN_REQUEST_INTERVAL <= 0:
        return
    with _PACE_LOCK:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.monotonic()


ACTIVE_STATUSES = ("queued", "running")


def _lease_prompt_suite(db, worker_id: str) -> R20PromptValidationSuite | None:
    now = utc_now_naive()
    suite = db.scalar(
        select(R20PromptValidationSuite)
        .where(
            R20PromptValidationSuite.protocol_id == PROTOCOL_ID,
            R20PromptValidationSuite.status.in_(ACTIVE_STATUSES),
            (R20PromptValidationSuite.lease_until.is_(None))
            | (R20PromptValidationSuite.lease_until < now),
        )
        .order_by(R20PromptValidationSuite.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if suite is None:
        return None
    suite.status = "running"
    suite.started_at = suite.started_at or now
    suite.worker_id = worker_id
    suite.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return suite


def _active_group(db, project_id: str) -> R20RunGroup | None:
    """Return the single active run group (question x condition) if any.

    One cell runs at a time: the queued/running group with the lowest
    order_rank.  The project-level start endpoint enforces the invariant, and
    ordering keeps concurrent workers converging on the same cell.
    """
    return db.scalar(
        select(R20RunGroup)
        .where(
            R20RunGroup.project_id == project_id,
            R20RunGroup.status.in_(ACTIVE_STATUSES),
        )
        .order_by(R20RunGroup.order_rank)
        .limit(1)
    )


def _group_has_active_lease(db, project_id: str, group: R20RunGroup) -> bool:
    """True when any stream in the group is currently mid-flight (leased)."""
    now = utc_now_naive()
    return (
        db.scalar(
            select(R20Stream.id)
            .where(
                R20Stream.project_id == project_id,
                R20Stream.question_id == group.question_id,
                R20Stream.condition == group.condition,
                R20Stream.status == "leased",
                R20Stream.lease_until > now,
            )
            .limit(1)
        )
        is not None
    )


def _lease(db, worker_id: str):
    """Lease a stream from the active run group for new work.

    Only one stream in the active group is leased at a time: a second lease is
    refused while any stream in the group is mid-flight, so the group (and with
    one active group at a time, the whole experiment) runs strictly serially.
    A pending retry or a terminal failure in the active group also pauses its
    new calls until every retry has succeeded and every terminal failure has
    been manually retried.
    """
    now = utc_now_naive()
    for project in db.scalars(
        select(R20Project)
        .where(
            R20Project.protocol_id == PROTOCOL_ID,
            R20Project.status == "running",
        )
        .limit(1)
    ):
        group = _active_group(db, project.id)
        if group is None:
            continue
        # Serialize concurrent workers on the group row so the mid-flight check
        # below cannot race: two threads can never lease two streams at once.
        group = db.scalar(
            select(R20RunGroup).where(R20RunGroup.id == group.id).with_for_update()
        )
        if _group_has_active_lease(db, project.id, group):
            db.commit()
            continue
        stream = db.scalar(
            select(R20Stream)
            .where(
                R20Stream.project_id == project.id,
                R20Stream.question_id == group.question_id,
                R20Stream.condition == group.condition,
                R20Stream.status.in_(("pending", "leased")),
                (R20Stream.lease_until.is_(None)) | (R20Stream.lease_until < now),
                ~(
                    select(R20Call.id)
                    .where(
                        R20Call.project_id == project.id,
                        R20Call.question_id == group.question_id,
                        R20Call.condition == group.condition,
                        R20Call.status.in_(("retry_pending", "failed_terminal")),
                    )
                    .exists()
                ),
            )
            .order_by(R20Stream.order_rank)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if stream is None:
            db.commit()  # Release group row lock before continuing
            continue
        stream.status, stream.worker_id, stream.lease_until = (
            "leased",
            worker_id,
            now + timedelta(seconds=LEASE_SECONDS),
        )
        db.commit()
        return stream
    return None


def _lease_retry(db, worker_id: str):
    """Lease the stream owning the earliest due retry_pending call.

    Retries take priority over new work so the experiment stays paused until
    the failed call succeeds.
    """
    now = utc_now_naive()
    project = db.scalar(
        select(R20Project)
        .where(
            R20Project.protocol_id == PROTOCOL_ID,
            R20Project.status == "running",
        )
        .limit(1)
    )
    if project is None:
        return None
    group = _active_group(db, project.id)
    if group is None:
        return None
    # Serialize concurrent workers on the group row so the mid-flight check
    # below cannot race: two threads can never lease two streams at once.
    group = db.scalar(
        select(R20RunGroup).where(R20RunGroup.id == group.id).with_for_update()
    )
    if _group_has_active_lease(db, project.id, group):
        db.commit()
        return None
    stream = db.scalar(
        select(R20Stream)
        .where(
            R20Stream.project_id == project.id,
            R20Stream.question_id == group.question_id,
            R20Stream.condition == group.condition,
            (
                select(R20Call.id)
                .where(
                    R20Call.project_id == project.id,
                    R20Call.model_id == R20Stream.model_id,
                    R20Call.question_id == R20Stream.question_id,
                    R20Call.condition == R20Stream.condition,
                    R20Call.trajectory == R20Stream.trajectory,
                    R20Call.status == "retry_pending",
                    (R20Call.next_retry_at.is_(None)) | (R20Call.next_retry_at <= now),
                )
                .exists()
            ),
        )
        .order_by(R20Stream.order_rank)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not stream:
        db.commit()  # Release group row lock when nothing to lease
        return None
    stream.status, stream.worker_id, stream.lease_until = (
        "leased",
        worker_id,
        now + timedelta(seconds=LEASE_SECONDS),
    )
    db.commit()
    return stream


def _process(db, stream: R20Stream) -> None:
    project = db.get(R20Project, stream.project_id)
    if project.status != "running":
        stream.status, stream.worker_id, stream.lease_until = "pending", None, None
        db.commit()
        return
    retry_spec = next_retry(db, project, stream)
    if retry_spec is not None:
        retry_row = db.scalar(
            select(R20Call).where(
                R20Call.project_id == project.id,
                R20Call.model_id == stream.model_id,
                R20Call.question_id == stream.question_id,
                R20Call.condition == stream.condition,
                R20Call.trajectory == stream.trajectory,
                R20Call.kind == retry_spec.kind,
                R20Call.answer_id == retry_spec.answer_id,
                R20Call.history_count == retry_spec.history_count,
                R20Call.repeat == retry_spec.repeat,
                R20Call.status == "retry_pending",
            )
        )
        if retry_row is not None:
            _pace_request()
            execute_call(db, project, stream, retry_spec, retry_row=retry_row)
            db.refresh(stream)
            stream.status, stream.worker_id, stream.lease_until = "pending", None, None
            db.commit()
            return
    pending = db.scalar(
        select(R20Call).where(
            R20Call.project_id == project.id,
            R20Call.model_id == stream.model_id,
            R20Call.question_id == stream.question_id,
            R20Call.condition == stream.condition,
            R20Call.trajectory == stream.trajectory,
            R20Call.status == "pending",
        )
    )
    if pending is not None:
        pending.retry_count += 1
        if pending.retry_count >= 2:
            pending.status = "failed_terminal"
            pending.failure_reason = (
                "in-flight request state was ambiguous after lease recovery"
            )
            pending.next_retry_at = None
        else:
            pending.status = "retry_pending"
            pending.failure_reason = (
                "in-flight request state was ambiguous after lease recovery"
            )
            pending.next_retry_at = utc_now_naive() + timedelta(
                seconds=AUTO_RETRY_SECONDS
            )
        db.commit()
    spec = next_call(db, project, stream)
    if spec is None:
        if has_unrecorded(db, project, stream) or has_retry_pending(
            db, project, stream
        ):
            stream.status, stream.worker_id, stream.lease_until = "pending", None, None
            db.commit()
        else:
            complete_stream(db, project, stream)
        return
    _pace_request()
    execute_call(db, project, stream, spec)
    db.refresh(stream)
    stream.status, stream.worker_id, stream.lease_until = "pending", None, None
    db.commit()


def _loop(stop: threading.Event) -> None:
    worker_id = str(uuid.uuid4())
    while not stop.is_set():
        with SessionLocal() as db:
            suite = _lease_prompt_suite(db, worker_id)
            if suite is not None and not stop.is_set():
                try:
                    _pace_request()
                    process_validation_call(db, suite)
                except Exception as exc:
                    logger.exception("r20 prompt validation suite %s failed", suite.id)
                    db.rollback()
                    suite = db.get(R20PromptValidationSuite, suite.id)
                    if suite is not None:
                        suite.status = "failed"
                        suite.failure_reason = str(exc)
                        suite.completed_at = utc_now_naive()
                        suite.worker_id = None
                        suite.lease_until = None
                        db.commit()
                continue
            for project in db.scalars(
                select(R20Project)
                .where(
                    R20Project.protocol_id == PROTOCOL_ID,
                    R20Project.status == "running",
                )
                .limit(1)
            ):
                for group in db.scalars(
                    select(R20RunGroup)
                    .where(
                        R20RunGroup.project_id == project.id,
                        R20RunGroup.status == "queued",
                    )
                    .order_by(R20RunGroup.order_rank)
                    .limit(1)
                ):
                    group.status = "running"
                db.commit()
            stream = _lease_retry(db, worker_id) or _lease(db, worker_id)
            if stream and not stop.is_set():
                try:
                    _process(db, stream)
                except Exception:
                    logger.exception("r20 stream %s failed; releasing lease", stream.id)
                    db.rollback()
                    stream = db.get(R20Stream, stream.id)
                    if stream is not None:
                        stream.status, stream.worker_id, stream.lease_until = (
                            "pending",
                            None,
                            None,
                        )
                        db.commit()
        stop.wait(POLL_SECONDS)


def run_forever() -> None:
    stop = threading.Event()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda _signum, _frame: stop.set())
    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futures = [pool.submit(_loop, stop) for _ in range(CONCURRENCY)]
            for future in futures:
                future.result()
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("R20_LOG_LEVEL", "INFO"))
    run_forever()
