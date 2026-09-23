"""REST surface for the isolated SAF 2.0 memory-study protocols."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.experiment.memory_study.protocol import DEFAULT_PROTOCOL_ID, StudyCreate
from app.services.memory_study import MemoryStudyDomainError, MemoryStudyService

router = APIRouter()


class RunnerConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: str = Field(min_length=1, max_length=32)
    speed_mode: str = Field(min_length=1, max_length=24)
    timeout_seconds: int = Field(ge=1, le=3600)


class RunnerConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: str = Field(min_length=1, max_length=32)
    timeout_seconds: int = Field(ge=1, le=3600)


class SiteConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding_backend: str = Field(default="openai", pattern="^openai$")
    embedding_model: str = Field(min_length=1, max_length=200)
    embedding_revision: str = Field(min_length=1, max_length=80)
    embedding_api_base: str = Field(default="", max_length=400)
    # Optional on submit: an omitted key keeps the stored secret server-side.
    embedding_api_key: str = Field(default="", max_length=400)
    embedding_dims: int | None = Field(default=None, ge=1)


def service(db: Session = Depends(get_db)) -> MemoryStudyService:
    return MemoryStudyService(db)


def _call(fn):
    try:
        return fn()
    except MemoryStudyDomainError as exc:
        status = {
            "not_found": 404,
            "conflict": 409,
            "validation": 422,
            "data_audit_failed": 409,
            "protocol_drift": 409,
            "runtime_busy": 409,
            "embargoed": 423,
            "analysis_error": 500,
        }.get(exc.code, 400)
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/audit")
def audit(
    kind: str = Query("formal", pattern="^(development|pilot|formal)$"),
    protocol_id: str = Query(
        DEFAULT_PROTOCOL_ID,
        pattern="^saf-memory-framework-v3(?:-r2)?$",
    ),
    svc: MemoryStudyService = Depends(service),
):
    return _call(lambda: svc.audit(kind, protocol_id))


@router.get("/runtime")
def runtime(svc: MemoryStudyService = Depends(service)):
    return svc.runtime_status()


@router.get("/projects")
def projects(svc: MemoryStudyService = Depends(service)):
    return svc.list_projects()


@router.post("/projects", status_code=201)
def create_project(payload: StudyCreate, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.create_project(payload.model_dump()))


@router.get("/projects/{study_id}")
def project(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.get_project(study_id))


@router.post("/projects/{study_id}/freeze")
def freeze(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.freeze(study_id))


@router.post("/projects/{study_id}/preflight")
def preflight(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.preflight(study_id))


@router.post("/projects/{study_id}/start")
def start(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.start(study_id))


@router.post("/projects/{study_id}/pause")
def pause(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.pause(study_id))


@router.post("/projects/{study_id}/resume")
def resume(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.resume(study_id))


@router.post("/projects/{study_id}/terminate")
def terminate(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.terminate(study_id))


@router.get("/projects/{study_id}/questions")
def question_runs(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.list_question_runs(study_id))


@router.post("/projects/{study_id}/questions/{question_id}/start")
def start_question(study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.start_question(study_id, question_id))


@router.post("/projects/{study_id}/questions/{question_id}/pause")
def pause_question(study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.pause_question(study_id, question_id))


@router.post("/projects/{study_id}/questions/{question_id}/resume")
def resume_question(study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.resume_question(study_id, question_id))


@router.post("/projects/{study_id}/questions/{question_id}/restore")
def restore_question(study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.restore_question(study_id, question_id))


@router.post("/projects/{study_id}/questions/{question_id}/terminate")
def terminate_question(study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.terminate_question(study_id, question_id))


@router.post("/projects/{study_id}/audit")
def integrity_audit(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.audit_project(study_id))


@router.get("/projects/{study_id}/memory-stores")
def memory_stores(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.list_memory_stores(study_id))


@router.get("/projects/{study_id}/memory-stores/{store_id}")
def memory_store(
    study_id: str,
    store_id: int,
    svc: MemoryStudyService = Depends(service),
):
    return _call(lambda: svc.inspect_memory(study_id, store_id))


@router.post("/projects/{study_id}/memory-stores/{store_id}/rebuild")
def rebuild_memory_store(
    study_id: str,
    store_id: int,
    svc: MemoryStudyService = Depends(service),
):
    return _call(lambda: svc.recover_memory_store(study_id, store_id))


@router.get("/projects/{study_id}/calls")
def calls(
    study_id: str,
    kind: str | None = Query(None, pattern="^(memory_write|score)$"),
    status: str | None = Query(None, pattern="^(pending|leased|running|succeeded|failed|cancelled)$"),
    question_id: str | None = Query(None, max_length=128),
    model: str | None = Query(None, max_length=160),
    condition: str | None = Query(None, max_length=40),
    feedback_mode: str | None = Query(None, pattern="^(full|no_feedback)$"),
    cursor: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1_000),
    svc: MemoryStudyService = Depends(service),
):
    return _call(
        lambda: svc.list_calls(
            study_id,
            kind=kind,
            status=status,
            question_id=question_id,
            model=model,
            condition=condition,
            feedback_mode=feedback_mode,
            cursor=cursor,
            limit=limit,
        )
    )


@router.post("/projects/{study_id}/calls/{call_id}/retry")
def retry_call(study_id: str, call_id: int, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.retry_call(study_id, call_id))


@router.post("/projects/{study_id}/retry-failed")
def retry_failed(study_id: str, svc: MemoryStudyService = Depends(service)):
    """Return every failed call (and its memory store) to the pending queue."""
    return _call(lambda: svc.retry_all_failed(study_id))


@router.post("/projects/{study_id}/questions/{question_id}/retry-failed")
def retry_question_failed(
    study_id: str, question_id: str, svc: MemoryStudyService = Depends(service)
):
    return _call(lambda: svc.retry_all_failed(study_id, question_id=question_id))


@router.post("/projects/{study_id}/questions/{question_id}/calls/{call_id}/retry")
def retry_question_call(
    study_id: str,
    question_id: str,
    call_id: int,
    svc: MemoryStudyService = Depends(service),
):
    return _call(
        lambda: svc.retry_call(
            study_id, call_id, expected_question_id=question_id
        )
    )


@router.get("/projects/{study_id}/report")
def report(study_id: str, svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.report(study_id))


@router.get("/projects/{study_id}/exports/{kind}")
def export(study_id: str, kind: str, svc: MemoryStudyService = Depends(service)):
    content_type, filename, content = _call(lambda: svc.export(study_id, kind))
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/projects/{study_id}", status_code=204)
def delete_project(study_id: str, svc: MemoryStudyService = Depends(service)):
    _call(lambda: svc.delete_project(study_id))
    return Response(status_code=204)


@router.get("/config")
def protocol_config(svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.protocol_config())


@router.get("/runners")
def runners(svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.list_runner_configs())


@router.post("/runners", status_code=201)
def create_runner(
    payload: RunnerConfigPayload, svc: MemoryStudyService = Depends(service)
):
    return _call(lambda: svc.create_runner_config(payload.model_dump()))


@router.put("/runners/{model}/{speed_mode}")
def update_runner(
    model: str,
    speed_mode: str,
    payload: RunnerConfigUpdate,
    svc: MemoryStudyService = Depends(service),
):
    return _call(lambda: svc.update_runner_config(model, speed_mode, payload.model_dump()))


@router.delete("/runners/{model}/{speed_mode}", status_code=204)
def delete_runner(
    model: str, speed_mode: str, svc: MemoryStudyService = Depends(service)
):
    _call(lambda: svc.delete_runner_config(model, speed_mode))
    return Response(status_code=204)


@router.get("/site-config")
def site_config(svc: MemoryStudyService = Depends(service)):
    return _call(lambda: svc.get_site_config())


@router.put("/site-config")
def update_site_config(
    payload: SiteConfigUpdate, svc: MemoryStudyService = Depends(service)
):
    return _call(lambda: svc.update_site_config(payload.model_dump()))
