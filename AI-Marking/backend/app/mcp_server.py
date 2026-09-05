"""stdio MCP host for r21.

This module never writes application data to stdout.  The MCP SDK owns stdout
for JSON-RPC and Python logging is configured to stderr by the launcher.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi.encoders import jsonable_encoder
from mcp.server import MCPServer

from app.db.session import SessionLocal
from app.experiment.core.executor import run_one as exp_run_one
from app.experiment.r21 import PROTOCOL_ID
from app.experiment.r21.codex_runner import CodexExecRunner
from app.experiment.r21.executor import run_one
from app.experiment.r22 import PROTOCOL_ID as R22_PROTOCOL_ID
from app.experiment.r23 import PROTOCOL_ID as R23_PROTOCOL_ID
from app.experiment.r23.executor import run_one as r23_run_one
from app.experiment.r23.protocol import (
    DEFAULT_TIMEOUT_SECONDS as R23_DEFAULT_TIMEOUT_SECONDS,
)
from app.experiment.r23.protocol import (
    MAX_TIMEOUT_SECONDS as R23_MAX_TIMEOUT_SECONDS,
)
from app.experiment.r23.protocol import (
    MIN_TIMEOUT_SECONDS as R23_MIN_TIMEOUT_SECONDS,
)
from app.services.r21 import (
    RUNNER_HEARTBEAT_SECONDS,
    R21DomainError,
    R21Service,
)

logger = logging.getLogger("ai_marking.r21.mcp")


def _svc_call(method, *args, **kwargs):
    with SessionLocal() as db:
        try:
            return jsonable_encoder(method(R21Service(db), *args, **kwargs))
        except R21DomainError as exc:
            return {"error": {"code": exc.code, "message": str(exc)}}


def _lease_call(method: str, *args) -> bool:
    with SessionLocal() as db:
        return bool(getattr(R21Service(db), method)(*args))


async def _wait_or_timeout(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _heartbeat(
    stop: asyncio.Event,
    lost: asyncio.Event,
    runner: CodexExecRunner,
    worker_id: str,
) -> None:
    while not stop.is_set() and not lost.is_set():
        await _wait_or_timeout(stop, RUNNER_HEARTBEAT_SECONDS)
        if stop.is_set():
            return
        try:
            owned = await asyncio.to_thread(
                _lease_call, "heartbeat_runner_lease", worker_id
            )
        except Exception:
            logger.exception("r21 MCP heartbeat failed")
            owned = False
        if not owned:
            lost.set()
            runner.terminate_active()
            return


async def _runner_host(stop: asyncio.Event, runner: CodexExecRunner) -> None:
    """Own at most one global worker lease and execute queued calls serially."""
    worker_id = f"mcp-{uuid.uuid4().hex[:12]}"
    runtime = await asyncio.to_thread(runner.runtime_fingerprint)
    while not stop.is_set():
        try:
            acquired = await asyncio.to_thread(
                _lease_call, "acquire_runner_lease", worker_id, runtime
            )
        except Exception:
            logger.exception("r21 MCP lease acquisition failed")
            acquired = False
        if not acquired:
            await _wait_or_timeout(stop, RUNNER_HEARTBEAT_SECONDS)
            continue

        lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            _heartbeat(stop, lost, runner, worker_id), name="r21-mcp-heartbeat"
        )
        try:
            claim_sources: list[Any] = [exp_run_one, r23_run_one, run_one]
            rotation = 0
            while not stop.is_set() and not lost.is_set():
                ran = False
                for offset in range(len(claim_sources)):
                    claim = claim_sources[(rotation + offset) % len(claim_sources)]
                    try:
                        with SessionLocal() as db:
                            ran = await asyncio.to_thread(
                                claim,
                                db,
                                worker_id=worker_id,
                                runner_factory=lambda: runner,
                            )
                    except Exception:
                        logger.exception("MCP worker iteration failed")
                        ran = False
                    if ran:
                        break
                if ran:
                    # One Codex subprocess at a time, rotating claim priority.
                    rotation = (rotation + 1) % len(claim_sources)
                else:
                    await _wait_or_timeout(stop, 5.0)
        finally:
            lost.set()
            runner.terminate_active()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            try:
                await asyncio.to_thread(_lease_call, "release_runner_lease", worker_id)
            except Exception:
                logger.exception("r21 MCP lease release failed")


@asynccontextmanager
async def lifespan(_: MCPServer):
    stop = asyncio.Event()
    runner = CodexExecRunner()
    task = asyncio.create_task(_runner_host(stop, runner), name="r21-mcp-runner-host")
    try:
        yield {}
    finally:
        stop.set()
        runner.terminate_active()
        try:
            await asyncio.wait_for(task, timeout=15)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


server = MCPServer(
    "ai-marking-r21",
    version="21.0.0",
    instructions=(
        "r21 is a blinded, single-model Codex CLI experiment. Create and freeze a "
        "runner and prompt, complete a technical pilot, then create a matching formal "
        "project. The browser creates, freezes, starts, pauses, resumes, retries, and "
        "terminates projects. One confirmed project start automatically runs every group "
        "in frozen order. Start/freeze/retry/terminate/delete require confirm=true. "
        "Queued work runs only while one MCP host owns the global runner lease; use "
        "low-frequency status queries and do not infer ARM−CRM direction from pilot "
        "reporting."
    ),
    lifespan=lifespan,
)


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_get_runtime_status() -> dict[str, Any]:
    return _svc_call(lambda svc: svc.runtime_status())


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_runner_configs() -> list[dict[str, Any]]:
    return _svc_call(lambda svc: svc.list_runner_configs())


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_prompt_versions() -> list[dict[str, Any]]:
    return _svc_call(lambda svc: svc.list_prompt_versions())


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_projects() -> list[dict[str, Any]]:
    return _svc_call(lambda svc: svc.list_projects())


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_get_project(project_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.get_project(project_id))


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_run_groups(project_id: str) -> list[dict[str, Any]]:
    return _svc_call(lambda svc: svc.list_groups(project_id))


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_get_report(project_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.get_report(project_id))


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_calls(project_id: str, limit: int = 200) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.list_calls(project_id, limit=limit))


@server.tool(annotations={"readOnlyHint": True}, structured_output=True)
def r21_list_failures(project_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.list_calls(project_id, failures_only=True))


@server.tool(structured_output=True)
def r21_create_runner_config(
    name: str, model: str, reasoning_effort: str, timeout_seconds: int = 600
) -> dict[str, Any]:
    return _svc_call(
        lambda svc: svc.create_runner_config(
            {
                "name": name,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "timeout_seconds": timeout_seconds,
            }
        )
    )


@server.tool(structured_output=True)
def r21_update_runner_config(
    config_id: str,
    name: str,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    return _svc_call(
        lambda svc: svc.update_runner_config(
            config_id,
            {
                "name": name,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "timeout_seconds": timeout_seconds,
            },
        )
    )


@server.tool(structured_output=True)
def r21_clone_runner_config(config_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.clone_runner_config(config_id))


@server.tool(structured_output=True)
def r21_freeze_runner_config(config_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.freeze_runner_config(config_id))


@server.tool(annotations={"destructiveHint": True}, structured_output=True)
def r21_delete_runner_config(config_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    _svc_call(lambda svc: svc.delete_runner_config(config_id))
    return {"deleted": config_id}


@server.tool(structured_output=True)
def r21_create_prompt_version(
    name: str, scoring: str, crm_update: str, arm_update: str
) -> dict[str, Any]:
    return _svc_call(
        lambda svc: svc.create_prompt_version(
            name=name,
            templates={
                "scoring": scoring,
                "crm_update": crm_update,
                "arm_update": arm_update,
            },
        )
    )


@server.tool(structured_output=True)
def r21_freeze_prompt_version(version_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.freeze_prompt_version(version_id))


@server.tool(annotations={"destructiveHint": True}, structured_output=True)
def r21_delete_prompt_version(version_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    _svc_call(lambda svc: svc.delete_prompt_version(version_id))
    return {"deleted": version_id}


@server.tool(structured_output=True)
def r21_create_project(
    name: str,
    kind: str,
    runner_config_id: str,
    prompt_version_id: str,
    pilot_project_id: str | None = None,
) -> dict[str, Any]:
    return _svc_call(
        lambda svc: svc.create_project(
            {
                "name": name,
                "kind": kind,
                "runner_config_id": runner_config_id,
                "prompt_version_id": prompt_version_id,
                "pilot_project_id": pilot_project_id,
            }
        )
    )


@server.tool(structured_output=True)
def r21_freeze_project(project_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.freeze_project(project_id))


@server.tool(structured_output=True)
def r21_start_project(project_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.start_project(project_id))


@server.tool(structured_output=True)
def r21_pause_project(project_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.pause_project(project_id))


@server.tool(structured_output=True)
def r21_resume_project(project_id: str) -> dict[str, Any]:
    return _svc_call(lambda svc: svc.resume_project(project_id))


@server.tool(annotations={"destructiveHint": True}, structured_output=True)
def r21_terminate_project(project_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.terminate_project(project_id))


@server.tool(annotations={"destructiveHint": True}, structured_output=True)
def r21_delete_project(project_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    _svc_call(lambda svc: svc.delete_project(project_id))
    return {"deleted": project_id}


@server.tool(structured_output=True)
def r21_retry_call(
    project_id: str, call_id: int, confirm: bool = False
) -> dict[str, Any]:
    if not confirm:
        return {"error": {"code": "validation", "message": "confirm=true is required"}}
    return _svc_call(lambda svc: svc.retry_call(project_id, call_id))


@server.resource("ai-marking://r21/protocol", mime_type="application/json")
def protocol_resource() -> str:
    return json.dumps(
        {
            "protocol_id": PROTOCOL_ID,
            "pilot_calls": 510,
            "formal_calls": 5760,
            "total_calls": 6270,
            "model_count": 1,
        },
        ensure_ascii=False,
    )


@server.resource("ai-marking://r22/protocol", mime_type="application/json")
def r22_protocol_resource() -> str:
    return json.dumps(
        {
            "protocol_id": R22_PROTOCOL_ID,
            "pilot_questions": ["4.13", "5.7"],
            "training_answers_per_question": 40,
            "test_answers_per_question": 10,
            "pilot_primary_calls": 2640,
            "calls_per_question": 1320,
            "model_count": 1,
            "compression": "at most one recovery call per complete token-overlong JSON",
        },
        ensure_ascii=False,
    )


@server.resource("ai-marking://r23/protocol", mime_type="application/json")
def r23_protocol_resource() -> str:
    return json.dumps(
        {
            "protocol_id": R23_PROTOCOL_ID,
            "pilot_observation_slots_per_model": 175,
            "formal_primary_slots_per_model": 2307,
            "formal_rerun_slots_per_model": 228,
            "model_count": 1,
            "reasoning_effort_options": ["low", "medium", "high"],
            "timeout_seconds": {
                "default": R23_DEFAULT_TIMEOUT_SECONDS,
                "minimum": R23_MIN_TIMEOUT_SECONDS,
                "maximum": R23_MAX_TIMEOUT_SECONDS,
            },
            "repeat_mode": "independent_project",
            "results_embargoed_until_locked_report": True,
        },
        ensure_ascii=False,
    )


@server.resource("ai-marking://r21/runtime", mime_type="application/json")
def runtime_resource() -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.runtime_status()), ensure_ascii=False, default=str
    )


@server.resource("ai-marking://r21/runner-configs", mime_type="application/json")
def runners_resource() -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.list_runner_configs()),
        ensure_ascii=False,
        default=str,
    )


@server.resource("ai-marking://r21/prompt-versions", mime_type="application/json")
def prompts_resource() -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.list_prompt_versions()),
        ensure_ascii=False,
        default=str,
    )


@server.resource("ai-marking://r21/projects", mime_type="application/json")
def projects_resource() -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.list_projects()), ensure_ascii=False, default=str
    )


@server.resource("ai-marking://r21/projects/{project_id}", mime_type="application/json")
def project_resource(project_id: str) -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.get_project(project_id)),
        ensure_ascii=False,
        default=str,
    )


@server.resource(
    "ai-marking://r21/projects/{project_id}/groups", mime_type="application/json"
)
def groups_resource(project_id: str) -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.list_groups(project_id)),
        ensure_ascii=False,
        default=str,
    )


@server.resource(
    "ai-marking://r21/projects/{project_id}/report", mime_type="application/json"
)
def report_resource(project_id: str) -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.get_report(project_id)),
        ensure_ascii=False,
        default=str,
    )


@server.resource(
    "ai-marking://r21/projects/{project_id}/failures", mime_type="application/json"
)
def failures_resource(project_id: str) -> str:
    return json.dumps(
        _svc_call(lambda svc: svc.list_calls(project_id, failures_only=True)),
        ensure_ascii=False,
        default=str,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
