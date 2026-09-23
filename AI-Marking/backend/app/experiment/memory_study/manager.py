"""Process-local lifecycle manager for web-started SAF memory workers.

The browser can request a run through the API, but it must never spawn Codex
directly.  This manager keeps the worker attached to the backend process while
the worker itself owns the serial Codex slot and the database lease.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Callable
from uuid import uuid4

from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.experiment.memory_study.memory import _redact_text
from app.experiment.memory_study.protocol import StudyStatus
from app.experiment.memory_study.worker import MemoryStudyWorker
from app.models.memory_study import MSSchedulerRuntime, MSStudy

logger = logging.getLogger("ai_marking.memory_study.manager")


@dataclass
class _WorkerHandle:
    worker_id: str
    thread: Thread
    worker: MemoryStudyWorker


class MemoryStudyWorkerManager:
    """Start at most one in-process worker per study.

    The worker's database lease remains the cross-process concurrency guard.
    The local registry only prevents duplicate starts from repeated browser
    clicks within the same backend process.
    """

    def __init__(
        self, worker_factory: Callable[[], MemoryStudyWorker] = MemoryStudyWorker
    ) -> None:
        self.worker_factory = worker_factory
        self._lock = Lock()
        self._handles: dict[str, _WorkerHandle] = {}

    def start(self, study_id: str) -> str:
        with self._lock:
            existing = self._handles.get(study_id)
            if existing and existing.thread.is_alive():
                return existing.worker_id

            worker_id = f"web-memory-study-{uuid4().hex[:12]}"
            worker = self.worker_factory()
            thread = Thread(
                target=self._run,
                args=(study_id, worker_id, worker),
                name=f"memory-study-{study_id[:12]}",
                daemon=True,
            )
            self._handles[study_id] = _WorkerHandle(worker_id, thread, worker)
            thread.start()
            return worker_id

    def is_running(self, study_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(study_id)
            return bool(handle and handle.thread.is_alive())

    def terminate(self, study_id: str) -> None:
        """Cancel the active study and terminate any in-flight CLI children."""
        with self._lock:
            handle = self._handles.get(study_id)
        if handle is not None:
            request_stop = getattr(handle.worker, "request_stop", None)
            if callable(request_stop):
                request_stop(study_id, terminate=True)

    def pause(self, study_id: str) -> None:
        """Stop claiming new calls while allowing the current call to finish."""
        with self._lock:
            handle = self._handles.get(study_id)
        if handle is not None:
            request_stop = getattr(handle.worker, "request_stop", None)
            if callable(request_stop):
                request_stop(study_id, terminate=False)

    def _run(self, study_id: str, worker_id: str, worker: MemoryStudyWorker) -> None:
        try:
            worker.run(study_id, worker_id=worker_id)
        except Exception as exc:  # pragma: no cover - exercised by integration
            logger.exception("web-started memory-study worker failed")
            self._mark_attention_required(study_id, str(exc), worker_id=worker_id)
        finally:
            with self._lock:
                handle = self._handles.get(study_id)
                if handle and handle.worker_id == worker_id:
                    self._handles.pop(study_id, None)

    @staticmethod
    def _mark_attention_required(
        study_id: str, message: str, *, worker_id: str | None = None
    ) -> None:
        try:
            with SessionLocal() as db:
                runtime = db.get(MSSchedulerRuntime, 1)
                now = utc_now_naive()
                # A second process can fail at the lease acquisition step
                # while the first worker is legitimately running.  Do not let
                # that rejected contender flip the active study to
                # ``attention_required``; only the lease owner may report its
                # own runtime failure.
                if (
                    runtime is not None
                    and runtime.owner_id
                    and runtime.owner_id != worker_id
                    and runtime.lease_until is not None
                    and runtime.lease_until >= now
                ):
                    return
                study = db.get(MSStudy, study_id)
                if study is not None and study.status == StudyStatus.running:
                    study.status = StudyStatus.attention_required
                    study.error_summary = f"worker failed: {_redact_text(message, 2000)}"
                    db.commit()
        except Exception:
            logger.exception("could not persist memory-study worker failure")


memory_study_worker_manager = MemoryStudyWorkerManager()
