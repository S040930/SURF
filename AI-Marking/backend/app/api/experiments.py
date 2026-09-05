"""REST adapter for the unified experiment platform (``/api/experiments``)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.experiments import (
    ExperimentDomainError,
    ExperimentService,
    RunnerConfigIn,
)

router = APIRouter()


class RunnerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    speed_mode: Literal["standard", "fast"] = "standard"
    timeout_seconds: int = Field(default=120, ge=30, le=1_800)


class RubricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    rubric: str = Field(min_length=80, max_length=24_000)


class ProjectCreate(BaseModel):
    """Sampling size/seed are intentionally absent and template-fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["pilot_run", "formal"]
    template_id: str = Field(min_length=1)
    dataset_revision_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    runner_config_ids: list[str] = Field(min_length=1, max_length=4)
    pilot_project_id: str | None = None
    data_processing_confirmed: Literal[True]


def service(db: Session = Depends(get_db)) -> ExperimentService:
    return ExperimentService(db)


def _call(fn):
    try:
        return fn()
    except ExperimentDomainError as exc:
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


@router.get("/datasets")
def datasets(svc: ExperimentService = Depends(service)):
    """Registered datasets with their current gate status."""
    from app.experiment.core.registry import list_datasets

    result = []
    for adapter in list_datasets():
        result.append(_call(lambda adapter=adapter: svc.data_status(adapter.key)))
    return result


@router.get("/datasets/{dataset_key}/data-status")
def dataset_status(dataset_key: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.data_status(dataset_key))


@router.get("/datasets/{dataset_key}/revision")
def dataset_revision(dataset_key: str, svc: ExperimentService = Depends(service)):
    """Idempotently pin the current dataset state as a revision (no side effects
    on restricted files beyond the read-only audit)."""
    return _call(lambda: _revision_out(svc, dataset_key))


def _revision_out(svc: ExperimentService, dataset_key: str) -> dict:
    dataset_row, revision_row, contract_row = svc.ensure_dataset_revision(dataset_key)
    return {
        "dataset": {
            "id": dataset_row.id,
            "key": dataset_row.key,
            "name": dataset_row.name,
            "access_level": dataset_row.access_level,
            "license_note": dataset_row.license_note,
        },
        "revision": {
            "id": revision_row.id,
            "revision_label": revision_row.revision_label,
            "audit": revision_row.audit_json,
            "status": revision_row.status,
        },
        "contract": {
            "id": contract_row.id,
            "channels": contract_row.channels_json,
            "grid_min_x2": contract_row.grid_min_x2,
            "grid_max_x2": contract_row.grid_max_x2,
            "schema_sha256": contract_row.schema_sha256,
        },
    }


@router.get("/templates")
def templates(svc: ExperimentService = Depends(service)):
    return _call(svc.list_templates)


@router.get("/runtime")
def runtime(db: Session = Depends(get_db)):
    """Expose the shared MCP worker lease on the unified API surface."""
    from app.services.r21 import R21Service

    return R21Service(db).runtime_status()


@router.get("/runner-configs")
def runner_configs(svc: ExperimentService = Depends(service)):
    return _call(svc.list_runner_configs)


@router.post("/runner-configs", status_code=201)
def create_runner(payload: RunnerCreate, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.create_runner_config(payload.model_dump()))


@router.delete("/runner-configs/{config_id}", status_code=204)
def delete_runner(
    config_id: str,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_runner_config(config_id))


@router.get("/rubrics")
def rubrics(
    template_id: str | None = Query(None),
    svc: ExperimentService = Depends(service),
):
    return _call(lambda: svc.list_rubrics(template_id=template_id))


@router.post("/rubrics", status_code=201)
def create_rubric(payload: RubricCreate, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.create_rubric(payload.model_dump()))


@router.delete("/rubrics/{rubric_id}", status_code=204)
def delete_rubric(
    rubric_id: str,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_rubric(rubric_id))


@router.get("/projects")
def projects(svc: ExperimentService = Depends(service)):
    return _call(svc.list_projects)


@router.get("/projects/{project_id}")
def project(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.get_project(project_id))


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.create_project(payload.model_dump()))


@router.post("/projects/{project_id}/start")
def start_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.start_project(project_id))


@router.post("/projects/{project_id}/pause")
def pause_project(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.pause_project(project_id))


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.resume_project(project_id))


@router.post("/projects/{project_id}/terminate")
def terminate_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.terminate_project(project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    _call(lambda: svc.delete_project(project_id))


@router.get("/projects/{project_id}/groups")
def groups(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.list_groups(project_id))


@router.get("/projects/{project_id}/calls")
def calls(
    project_id: str,
    limit: int = Query(500, ge=1, le=2_000),
    svc: ExperimentService = Depends(service),
):
    return _call(lambda: svc.list_calls(project_id, limit=limit))


@router.post("/projects/{project_id}/calls/{call_id}/retry")
def retry_call(
    project_id: str,
    call_id: int,
    confirm: bool = Query(False),
    svc: ExperimentService = Depends(service),
):
    _confirm(confirm)
    return _call(lambda: svc.retry_call(project_id, call_id))


@router.get("/projects/{project_id}/report")
def report(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.get_report(project_id))


@router.get("/projects/{project_id}/analysis")
def analysis(project_id: str, svc: ExperimentService = Depends(service)):
    return _call(lambda: svc.get_analysis(project_id))


def _download(payload, *, media_type: str, filename: str, sha256: str) -> Response:
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
def export_manifest(project_id: str, svc: ExperimentService = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_manifest(project_id))
    return _download(
        payload,
        media_type="application/json",
        filename=f"{project_id}-manifest.json",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/results")
def export_results(project_id: str, svc: ExperimentService = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_results_csv(project_id))
    return _download(
        payload,
        media_type="text/csv; charset=utf-8",
        filename=f"{project_id}-results.csv",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/report")
def export_report(project_id: str, svc: ExperimentService = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_report_json(project_id))
    return _download(
        payload,
        media_type="application/json",
        filename=f"{project_id}-report.json",
        sha256=sha256,
    )


@router.get("/projects/{project_id}/exports/figures")
def export_figures(project_id: str, svc: ExperimentService = Depends(service)):
    payload, sha256 = _call(lambda: svc.export_figures_zip(project_id))
    return _download(
        payload,
        media_type="application/zip",
        filename=f"{project_id}-figures.zip",
        sha256=sha256,
    )


__all__ = ["ProjectCreate", "RunnerConfigIn", "router"]
