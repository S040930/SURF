"""Run one preflighted/running Luna-only v3 memory study with four slots.

``--study-id auto`` adopts every study currently marked ``running`` that no
live worker owns, one after another.  This is what the OS-level supervision
loop (see ``start.sh``) re-executes when the executor process dies: a fresh
process picks the interrupted studies back up without any operator input.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    # Make the documented ``python scripts/run_memory_study.py`` invocation
    # resolve the backend's ``app`` package from any working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.experiment.memory_study.manager import MemoryStudyWorker
from app.experiment.memory_study.protocol import StudyStatus
from app.experiment.memory_study.worker import _worker_alive
from app.models.memory_study import MSSchedulerRuntime, MSStudy

logger = logging.getLogger("ai_marking.memory_study.runner")


def _studies_to_adopt() -> list[str]:
    """Return running studies whose scheduler lease is dead or foreign.

    A study is adoptable when its marked status is ``running`` but no live
    lease exists (the previous executor process died), or when the lease has
    already expired past the grace window.  A lease still held by another
    live process is left alone.  A lease whose holder process is verifiably
    dead (crash left ``status="online"`` behind) is treated as dead
    immediately instead of waiting out the full 600s expiry.
    """
    now = utc_now_naive()
    with SessionLocal() as db:
        runtime = db.get(MSSchedulerRuntime, 1)
        lease_live = bool(
            runtime is not None
            and runtime.lease_until is not None
            and runtime.lease_until >= now
        )
        if lease_live and not _worker_alive(
            runtime.worker_pid, runtime.worker_host
        ):
            logger.info(
                "lease holder pid=%s is dead; adopting its studies immediately",
                runtime.worker_pid,
            )
            lease_live = False
        if lease_live:
            return []
        return list(
            db.scalars(
                select(MSStudy.id).where(MSStudy.status == StudyStatus.running)
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--worker-id")
    parser.add_argument(
        "--auto-rescan-seconds",
        type=int,
        default=30,
        help="with --study-id auto: how long to wait before re-scanning for "
        "adoptable studies (0 exits after one pass)",
    )
    parser.add_argument(
        "--auto-once",
        action="store_true",
        help="with --study-id auto: adopt once, then exit instead of looping",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = MemoryStudyWorker()

    if args.study_id != "auto":
        worker.run(args.study_id, worker_id=args.worker_id)
        return

    rescan = max(0, args.auto_rescan_seconds)
    while True:
        study_ids = _studies_to_adopt()
        if not study_ids:
            if args.auto_once or rescan == 0:
                return
            time.sleep(rescan)
            continue
        for study_id in study_ids:
            logger.info("adopting running study %s", study_id)
            try:
                worker.run(study_id, worker_id=args.worker_id)
            except Exception:
                # 一个研究的运行时问题（preflight 失效等）不应阻止其他
                # running 研究被认领；监督循环会按节奏再试。
                logger.exception("failed to adopt study %s", study_id)
        if args.auto_once or rescan == 0:
            return
        time.sleep(rescan)


if __name__ == "__main__":
    main()
