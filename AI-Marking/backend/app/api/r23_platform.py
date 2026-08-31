"""REST adapter for the isolated r23 DREsS_CASE platform."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.experiment.r23.protocol import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    R23Score,
)
from app.services.r21 import R21Service
from app.services.r23 import R23DomainError, R23Service

router = APIRouter()


class R23RunnerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    speed_mode: Literal["standard", "fast"] = "standard"
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=MIN_TIMEOUT_SECONDS,
        le=MAX_TIMEOUT_SECONDS,
    )


class R23RubricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    rubric: str = Field(min_length=80, max_length=24_000)


class R23ProjectCreate(BaseModel):
    """Sampling size/seed are intentionally absent and protocol-fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["pilot_run", "formal"]
    runner_config_id: str = Field(min_length=1)
    rubric_id: str
    pilot_project_id: str | None = None
    data_processing_confirmed: Literal[True]


def service(db: Session = Depends(get_db)) -> R23Service:
    return R23Service(db)


def _call(fn):
    try:
        return fn()
    except R23DomainError as exc:
        status = {
            "not_found": 404,
            "conflict": 409,
            "forbidden": 403,
            "validation": 422,
            "runner_unavailable": 503,
            "runner_drift": 409,
            "data_drift": 409,
            "embargoed": 423,
            "analysis_error": 500,
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


@router.get("/data-status")
def data_status(svc: R23Service = Depends(service)):
    return _call(svc.data_status)


@router.get("/runtime")
def runtime(db: Session = Depends(get_db)):
    """Expose the shared MCP worker lease on the r23 API surface."""
    return R21Service(db).runtime_status()


@router.get("/runner-configs")
def runner_configs(svc: R23Service = Depends(service)):
    return _call(svc.list_runner_configs)


@router.get("/runner-configs/{config_id}")
def runner_config(config_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.runner_out(svc._runner(config_id)))


@router.post("/runner-configs", status_code=201)
def create_runner(payload: R23RunnerCreate, svc: R23Service = Depends(service)):
    return _call(lambda: svc.create_runner_config(payload.model_dump()))


@router.delete("/runner-configs/{config_id}", status_code=204)
def delete_runner(
    config_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_runner_config(config_id))


@router.get("/rubrics")
def rubrics(svc: R23Service = Depends(service)):
    return _call(svc.list_rubrics)


@router.get("/rubrics/{rubric_id}")
def rubric(rubric_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.rubric_out(svc._rubric(rubric_id)))


@router.post("/rubrics", status_code=201)
def create_rubric(payload: R23RubricCreate, svc: R23Service = Depends(service)):
    return _call(lambda: svc.create_rubric(payload.model_dump()))


@router.delete("/rubrics/{rubric_id}", status_code=204)
def delete_rubric(
    rubric_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_rubric(rubric_id))


@router.get("/projects")
def projects(svc: R23Service = Depends(service)):
    return _call(svc.list_projects)


@router.get("/projects/{project_id}")
def project(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.get_project(project_id))


@router.post("/projects", status_code=201)
def create_project(payload: R23ProjectCreate, svc: R23Service = Depends(service)):
    return _call(lambda: svc.create_project(payload.model_dump()))


@router.post("/projects/{project_id}/start")
def start_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.start_project(project_id))


@router.post("/projects/{project_id}/repeat", status_code=201)
def repeat_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.repeat_project(project_id))


@router.post("/projects/{project_id}/pause")
def pause_project(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.pause_project(project_id))


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.resume_project(project_id))


@router.post("/projects/{project_id}/terminate")
def terminate_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.terminate_project(project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_project(project_id))


@router.get("/projects/{project_id}/groups")
def groups(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.list_groups(project_id))


@router.get("/projects/{project_id}/calls")
def calls(
    project_id: str,
    limit: int = Query(500, ge=1, le=2_000),
    svc: R23Service = Depends(service),
):
    return _call(lambda: svc.list_calls(project_id, limit=limit))


@router.post("/projects/{project_id}/calls/{call_id}/retry")
def retry_call(
    project_id: str,
    call_id: int,
    confirm: bool = Query(False),
    svc: R23Service = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.retry_call(project_id, call_id))


@router.get("/projects/{project_id}/report")
def report(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.get_report(project_id))


@router.get("/projects/{project_id}/analysis")
def analysis(project_id: str, svc: R23Service = Depends(service)):
    return _call(lambda: svc.get_analysis(project_id))


def _download(payload: str, *, media_type: str, filename: str, sha256: str) -> Response:
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": sha256,
            "Cache-Control": "no-store",
        },
    )


@router.get("/projects/{project_id}/exports/manifest")
def export_manifest(project_id: str, svc: R23Service = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_manifest(project_id))
    return _download(
        payload,
        media_type="application/json",
        filename=f"{project_id}-manifest.json",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/results")
def export_results(project_id: str, svc: R23Service = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_results_csv(project_id))
    return _download(
        payload,
        media_type="text/csv; charset=utf-8",
        filename=f"{project_id}-results.csv",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/report")
def export_report(project_id: str, svc: R23Service = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_report_json(project_id))
    return _download(
        payload,
        media_type="application/json",
        filename=f"{project_id}-report.json",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/figure")
def export_figure(project_id: str, svc: R23Service = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_figure_svg(project_id))
    return _download(
        payload,
        media_type="image/svg+xml",
        filename=f"{project_id}-figure.svg",
        sha256=sha256,
    )


__all__ = ["R23ProjectCreate", "R23Score", "router"]
