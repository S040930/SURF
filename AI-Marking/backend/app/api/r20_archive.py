"""Read-only r20 archive API.

The previous protocol remains inspectable but cannot create, freeze, execute,
retry, update, or delete anything after r21 becomes active.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.r20 import (
    R20Call,
    R20ModelConfig,
    R20Project,
    R20PromptVersion,
    R20Report,
    R20RunGroup,
)

router = APIRouter()


def _project(db: Session, project_id: str) -> R20Project:
    value = db.get(R20Project, project_id)
    if value is None:
        raise HTTPException(
            status_code=404, detail="r20 archive project does not exist"
        )
    return value


def _out(db: Session, project: R20Project) -> dict:
    counts = dict(
        db.execute(
            select(R20Call.status, func.count())
            .where(R20Call.project_id == project.id)
            .group_by(R20Call.status)
        ).all()
    )
    return {
        "id": project.id,
        "protocol_id": project.protocol_id,
        "name": project.name,
        "kind": project.kind,
        "status": project.status,
        "model_config_ids_json": project.model_config_ids_json,
        "prompt_version_id": project.prompt_version_id,
        "prompt_version_name": project.prompt_version_name,
        "pilot_project_id": project.pilot_project_id,
        "manifest_json": {"protocol": project.protocol_id, "blinded": True},
        "progress": {"recorded": sum(counts.values()), **counts},
        "created_at": project.created_at,
        "frozen_at": project.frozen_at,
        "started_at": project.started_at,
        "completed_at": project.completed_at,
    }


@router.get("/model-configs")
def configs(db: Session = Depends(get_db)):
    return list(
        db.scalars(select(R20ModelConfig).order_by(R20ModelConfig.created_at.desc()))
    )


@router.get("/prompt-versions")
def prompts(db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(R20PromptVersion).order_by(R20PromptVersion.created_at.desc())
        )
    )


@router.get("/projects")
def projects(db: Session = Depends(get_db)):
    return [
        _out(db, value)
        for value in db.scalars(
            select(R20Project).order_by(R20Project.created_at.desc())
        )
    ]


@router.get("/projects/{project_id}")
def project(project_id: str, db: Session = Depends(get_db)):
    return _out(db, _project(db, project_id))


@router.get("/projects/{project_id}/groups")
def groups(project_id: str, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(
        db.scalars(
            select(R20RunGroup)
            .where(R20RunGroup.project_id == project_id)
            .order_by(R20RunGroup.order_rank)
        )
    )


@router.get("/projects/{project_id}/report")
def report(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    saved = db.get(R20Report, project.id)
    return {
        "project_id": project.id,
        "status": project.status,
        "report": saved.report_json if saved else None,
        "report_sha256": saved.report_sha256 if saved else None,
    }


@router.get("/projects/{project_id}/calls")
def calls(
    project_id: str,
    limit: int = Query(200, ge=1, le=200),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id)
    if project.kind != "formal" or project.status != "completed":
        raise HTTPException(
            status_code=403, detail="archived call details remain blinded"
        )
    rows = list(
        db.scalars(
            select(R20Call)
            .where(R20Call.project_id == project.id)
            .order_by(R20Call.id.desc())
            .limit(limit)
        )
    )
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "model_id": row.model_id,
                "question_id": row.question_id,
                "condition": row.condition,
                "trajectory": row.trajectory,
                "history_count": row.history_count,
                "repeat": row.repeat,
                "output_json": row.output_json,
                "failure_reason": row.failure_reason,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/projects/{project_id}/failures")
def failures(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    rows = list(
        db.scalars(
            select(R20Call)
            .where(
                R20Call.project_id == project.id,
                R20Call.status.in_(("failed_terminal", "retry_pending")),
            )
            .order_by(R20Call.id)
        )
    )
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "model_id": row.model_id,
                "question_id": row.question_id,
                "condition": row.condition,
                "trajectory": row.trajectory,
                "history_count": row.history_count,
                "repeat": row.repeat,
                "failure_reason": row.failure_reason,
            }
            for row in rows
        ],
        "total": len(rows),
    }
