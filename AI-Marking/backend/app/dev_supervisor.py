"""Development supervisor for the local API only.

Experiment execution runs as an in-process worker thread inside the API
process; the browser never starts a local Codex process directly.  A source
change restarts the API (and therefore the worker); the worker reclaims any
expired call leases on the next start.
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from watchfiles import Change, watch

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"


@dataclass
class Generation:
    """One reloadable API process."""

    api: subprocess.Popen


def _is_python_change(change: Change, path: str) -> bool:
    del change
    candidate = Path(path)
    return candidate.suffix == ".py" and "__pycache__" not in candidate.parts


def _spawn(
    command: Sequence[str],
    *,
    cwd: Path = BACKEND_DIR,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> subprocess.Popen:
    return popen(list(command), cwd=str(cwd), env=os.environ.copy())


def _api_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]


def start_generation(
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> Generation:
    """Start the management API (which hosts the in-process worker)."""
    api = _spawn(_api_command(), popen=popen)
    generation = Generation(api=api)
    logger.info("API generation started: api_pid=%s", api.pid)
    return generation


def _stop_process(process: subprocess.Popen, label: str) -> None:
    """Ask one child to stop and wait without force-killing it."""
    if process.poll() is not None:
        return
    logger.info("stopping %s pid=%s", label, process.pid)
    try:
        process.terminate()
    except ProcessLookupError:
        return
    process.wait()


def stop_generation(generation: Generation) -> None:
    """Stop the API process and its worker threads."""
    _stop_process(generation.api, "API")
    logger.info("API generation stopped")


def _watch_changes(stop_event: threading.Event, changes: queue.Queue) -> None:
    try:
        for change_batch in watch(
            str(APP_DIR),
            watch_filter=_is_python_change,
            stop_event=stop_event,
        ):
            if stop_event.is_set():
                break
            changes.put(change_batch)
    except (KeyboardInterrupt, OSError):
        # The supervisor owns shutdown.  A watcher failure is surfaced by the
        # queue loop only if the runtime itself is still alive.
        if not stop_event.is_set():
            logger.exception("source watcher stopped unexpectedly")
            changes.put(None)


def _unexpected_exit(generation: Generation) -> str | None:
    api_code = generation.api.poll()
    if api_code is not None:
        return f"API exited unexpectedly with code {api_code}"
    return None


def run_supervisor() -> None:
    """Run one source-watched management API generation until shutdown."""
    stop_event = threading.Event()
    changes: queue.Queue = queue.Queue()
    previous_handlers: dict[int, object] = {}

    def request_stop(signum, _frame) -> None:
        logger.info("runtime supervisor received signal %s", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, request_stop)

    generation: Generation | None = None
    watcher = threading.Thread(
        target=_watch_changes,
        args=(stop_event, changes),
        name="api-source-watcher",
        daemon=True,
    )
    watcher.start()
    try:
        generation = start_generation()
        while not stop_event.is_set():
            if generation is None:
                raise RuntimeError("runtime generation was not started")
            failure = _unexpected_exit(generation)
            if failure:
                raise RuntimeError(failure)
            try:
                change_batch = changes.get(timeout=0.25)
            except queue.Empty:
                continue
            if change_batch is None:
                raise RuntimeError("source watcher exited unexpectedly")
            changed = ", ".join(sorted(str(path) for _change, path in change_batch))
            logger.info("source change detected (%s); draining generation", changed)
            stop_generation(generation)
            if stop_event.is_set():
                break
            generation = start_generation()
    finally:
        stop_event.set()
        if generation is not None:
            stop_generation(generation)
        watcher.join(timeout=2)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("R21_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_supervisor()
