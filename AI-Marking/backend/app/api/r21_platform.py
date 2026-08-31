"""REST adapter for the r21 Codex CLI protocol.

The browser can manage experiments through these endpoints, but it never starts
a model worker itself: queued work is consumed only by an attached MCP host.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.r21 import R21DomainError, R21Service

router = APIRouter()


class RunnerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: str = Field(min_length=1, max_length=24)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)


class PromptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scoring: str = Field(min_length=1, max_length=12000)
    crm_update: str = Field(min_length=1, max_length=12000)
    arm_update: str = Field(min_length=1, max_length=12000)


class ProjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["pilot_run", "formal"]
    runner_config_id: str
    prompt_version_id: str
    pilot_project_id: str | None = None


def service(db: Session = Depends(get_db)) -> R21Service:
    return R21Service(db)


def _call(fn):
    try:
        return fn()
    except R21DomainError as exc:
        status = {
            "not_found": 404,
            "conflict": 409,
            "forbidden": 403,
            "validation": 422,
            "runner_unavailable": 503,
            "runner_drift": 409,
        }.get(exc.code, 400)
        raise HTTPException(
            status_code=status, detail={"code": exc.code, "message": str(exc)}
        ) from exc


def _confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation", "message": "confirm=true is required"},
        )


@router.get("/runtime")
def runtime(svc: R21Service = Depends(service)):
    return _call(svc.runtime_status)


@router.get("/runner-configs")
def runner_configs(svc: R21Service = Depends(service)):
    return _call(svc.list_runner_configs)


@router.post("/runner-configs", status_code=201)
def create_runner(payload: RunnerPayload, svc: R21Service = Depends(service)):
    return _call(lambda: svc.create_runner_config(payload.model_dump()))


@router.put("/runner-configs/{config_id}")
def update_runner(
    config_id: str, payload: RunnerPayload, svc: R21Service = Depends(service)
):
    return _call(lambda: svc.update_runner_config(config_id, payload.model_dump()))


@router.post("/runner-configs/{config_id}/clone", status_code=201)
def clone_runner(config_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.clone_runner_config(config_id))


@router.post("/runner-configs/{config_id}/freeze")
def freeze_runner(
    config_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    return _call(lambda: svc.freeze_runner_config(config_id))


@router.delete("/runner-configs/{config_id}", status_code=204)
def delete_runner(
    config_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    _call(lambda: svc.delete_runner_config(config_id))


@router.get("/prompt-versions")
def prompt_versions(svc: R21Service = Depends(service)):
    return _call(svc.list_prompt_versions)


@router.post("/prompt-versions", status_code=201)
def create_prompt(payload: PromptPayload, svc: R21Service = Depends(service)):
    data = payload.model_dump()
    return _call(
        lambda: svc.create_prompt_version(name=data.pop("name"), templates=data)
    )


@router.post("/prompt-versions/{version_id}/freeze")
def freeze_prompt(
    version_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    return _call(lambda: svc.freeze_prompt_version(version_id))


@router.delete("/prompt-versions/{version_id}", status_code=204)
def delete_prompt(
    version_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    _call(lambda: svc.delete_prompt_version(version_id))


@router.get("/projects")
def projects(svc: R21Service = Depends(service)):
    return _call(svc.list_projects)


@router.get("/projects/{project_id}")
def project(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.get_project(project_id))


@router.post("/projects", status_code=201)
def create_project(payload: ProjectPayload, svc: R21Service = Depends(service)):
    return _call(lambda: svc.create_project(payload.model_dump()))


@router.post("/projects/{project_id}/freeze")
def freeze_project(
    project_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    return _call(lambda: svc.freeze_project(project_id))


@router.get("/projects/{project_id}/groups")
def groups(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.list_groups(project_id))


@router.post("/projects/{project_id}/start")
def start_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: R21Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.start_project(project_id))


@router.post("/projects/{project_id}/pause")
def pause(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.pause_project(project_id))


@router.post("/projects/{project_id}/resume")
def resume(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.resume_project(project_id))


@router.post("/projects/{project_id}/terminate")
def terminate(
    project_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    return _call(lambda: svc.terminate_project(project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str, confirm: bool = Query(False), svc: R21Service = Depends(service)
):
    _confirm(confirm)
    _call(lambda: svc.delete_project(project_id))


@router.get("/projects/{project_id}/report")
def report(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.get_report(project_id))


@router.get("/projects/{project_id}/calls")
def calls(
    project_id: str,
    limit: int = Query(200, ge=1, le=200),
    svc: R21Service = Depends(service),
):
    return _call(lambda: svc.list_calls(project_id, limit=limit))


@router.get("/projects/{project_id}/failures")
def failures(project_id: str, svc: R21Service = Depends(service)):
    return _call(lambda: svc.list_calls(project_id, failures_only=True))


@router.post("/projects/{project_id}/calls/{call_id}/retry")
def retry(
    project_id: str,
    call_id: int,
    confirm: bool = Query(False),
    svc: R21Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.retry_call(project_id, call_id))
