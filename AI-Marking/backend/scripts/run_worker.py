"""Standalone serial worker for long experiment runs.

Acquires the single global runner lease (``R21RunnerRuntime``) and executes
one pending call at a time across the same rotation as the MCP host: unified
``exp_`` evaluations, r23, then r21.  Useful when a multi-day formal run must
outlive a Codex session — run it from a terminal or tmux instead of hosting
the MCP server.  Run at most one worker at a time; the lease forbids a second.

Usage (from backend/):  .venv/bin/python -m scripts.run_worker
"""

from __future__ import annotations

import asyncio
import logging

from app.experiment.r21.codex_runner import CodexExecRunner
from app.mcp_server import _runner_host

logger = logging.getLogger("ai_marking.worker")


async def main() -> None:
    stop = asyncio.Event()
    runner = CodexExecRunner()
    logger.info("serial worker starting (Ctrl+C to stop; the lease is released on exit)")
    try:
        await _runner_host(stop, runner)
    except asyncio.CancelledError:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
