"""r20 SAF official-split experiment API."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.dataset import build_frozen_dataset
from app.experiment.r20.executor import call_sequence, expected_question_calls
from app.experiment.r20.prompt_validation import EXPECTED_VALIDATION_CALLS
from app.experiment.r20.prompts import (
    NORMATIVE_TEMPLATES_SHA256,
    mismatched_normative_fields,
    normalize_copied_templates,
    templates_hash,
)
from app.experiment.r20.protocol import PromptTemplates, schedule_for_kind
from app.experiment.secrets import delete_secret_by_name, set_secret_by_name
from app.experiment.types import ModelConfig
from app.models.r20 import (
    R20Call,
    R20CallAttempt,
    R20Exposure,
    R20ModelConfig,
    R20Project,
    R20PromptTrial,
    R20PromptValidationSuite,
    R20PromptVersion,
    R20Record,
    R20Report,
    R20RunGroup,
    R20Snapshot,
    R20Stream,
)
from app.schemas.r20 import (
    R20CallPageOut,
    R20ModelConfigIn,
    R20ModelConfigOut,
    R20ProjectCreate,
    R20ProjectOut,
    R20PromptValidationSuiteIn,
    R20PromptValidationSuiteOut,
    R20PromptVersionIn,
    R20PromptVersionOut,
    R20ReportOut,
    R20RunGroupOut,
)

router = APIRouter()

DELETE_DRAIN_TIMEOUT_SECONDS = 30.0
DELETE_DRAIN_POLL_SECONDS = 0.5


@router.get("/model-configs", response_model=list[R20ModelConfigOut])
def model_configs(db: Session = Depends(get_db)):
    return list(
        db.scalars(select(R20ModelConfig).order_by(R20ModelConfig.created_at.desc()))
    )


@router.post("/model-configs", response_model=R20ModelConfigOut, status_code=201)
def create_model_config(payload: R20ModelConfigIn, db: Session = Depends(get_db)):
    prepared = dict(payload.config_json)
    if payload.api_key and payload.api_key.strip():
        secret = f"EXPERIMENT_MODEL_API_KEY_{uuid.uuid4().hex.upper()}"
        set_secret_by_name(secret, payload.api_key.strip())
        prepared["api_key_env"] = secret
    try:
        config = ModelConfig.model_validate(prepared).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail="模型配置不是有效的服务配置"
        ) from exc
    row = R20ModelConfig(
        id=str(uuid.uuid4()),
        name=payload.name.strip(),
        config_json=config,
        config_sha256=_sha(config),
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/model-configs/{config_id}/freeze", response_model=R20ModelConfigOut)
def freeze_model_config(config_id: str, db: Session = Depends(get_db)):
    row = db.get(R20ModelConfig, config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="仅草稿模型可冻结")
    row.status = "frozen"
    row.frozen_at = utc_now_naive()
    db.commit()
    return row


@router.put("/model-configs/{config_id}", response_model=R20ModelConfigOut)
def update_model_config(
    config_id: str, payload: R20ModelConfigIn, db: Session = Depends(get_db)
):
    row = db.get(R20ModelConfig, config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="冻结模型配置不可修改")
    previous_secret = str(row.config_json.get("api_key_env", ""))
    prepared = dict(payload.config_json)
    if payload.api_key and payload.api_key.strip():
        secret = f"EXPERIMENT_MODEL_API_KEY_{uuid.uuid4().hex.upper()}"
        set_secret_by_name(secret, payload.api_key.strip())
        prepared["api_key_env"] = secret
    elif not prepared.get("api_key_env"):
        prepared["api_key_env"] = previous_secret
    try:
        config = ModelConfig.model_validate(prepared).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail="模型配置不是有效的服务配置"
        ) from exc
    row.name = payload.name.strip()
    row.config_json = config
    row.config_sha256 = _sha(config)
    db.commit()
    if previous_secret and previous_secret != config.get("api_key_env"):
        delete_secret_by_name(previous_secret)
    return row


@router.post("/model-configs/{config_id}/clone", response_model=R20ModelConfigOut)
def clone_model_config(config_id: str, db: Session = Depends(get_db)):
    source = db.get(R20ModelConfig, config_id)
    if source is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    row = R20ModelConfig(
        id=str(uuid.uuid4()),
        name=f"Copy of {source.name}",
        config_json=dict(source.config_json),
        config_sha256=source.config_sha256,
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/model-configs/{config_id}", status_code=204)
def delete_model_config(config_id: str, db: Session = Depends(get_db)):
    row = db.get(R20ModelConfig, config_id)
    if row is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    if any(
        config_id in ids for ids in db.scalars(select(R20Project.model_config_ids_json))
    ):
        raise HTTPException(status_code=409, detail="模型配置已被项目引用")
    if any(
        config_id in ids
        for ids in db.scalars(select(R20PromptValidationSuite.model_config_ids_json))
    ):
        raise HTTPException(status_code=409, detail="模型配置已被提示词验证记录引用")
    for trial in db.scalars(
        select(R20PromptTrial).where(R20PromptTrial.model_config_id == config_id)
    ):
        db.delete(trial)
    secret = str(row.config_json.get("api_key_env", ""))
    db.delete(row)
    db.commit()
    if secret and not any(
        str(config.get("api_key_env", "")) == secret
        for config in db.scalars(select(R20ModelConfig.config_json))
    ):
        delete_secret_by_name(secret)


@router.get("/prompt-versions", response_model=list[R20PromptVersionOut])
def prompt_versions(db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(R20PromptVersion).order_by(R20PromptVersion.created_at.desc())
        )
    )


@router.post("/prompt-versions", response_model=R20PromptVersionOut, status_code=201)
def create_prompt_version(payload: R20PromptVersionIn, db: Session = Depends(get_db)):
    submitted = PromptTemplates.model_validate(payload.model_dump(exclude={"name"}))
    templates = normalize_copied_templates(submitted)
    digest = templates_hash(templates)
    if digest != NORMATIVE_TEMPLATES_SHA256:
        labels = {
            "scoring": "共同评分提示词",
            "crm_update": "CRM 更新提示词",
            "arm_update": "ARM 更新提示词",
        }
        mismatches = "、".join(
            labels[name] for name in mismatched_normative_fields(templates)
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"以下正文与活动规范文档不一致：{mismatches}。"
                "系统已自动忽略复制产生的首尾空行及 CRLF/LF 差异；"
                "请检查正文中的空格、标点和 token 阈值。"
            ),
        )
    row = R20PromptVersion(
        id=str(uuid.uuid4()),
        protocol_id=PROTOCOL_ID,
        name=payload.name.strip(),
        templates_json=templates.model_dump(mode="json"),
        templates_sha256=digest,
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get(
    "/prompt-versions/{version_id}/validation-suites",
    response_model=list[R20PromptValidationSuiteOut],
)
def prompt_validation_suites(version_id: str, db: Session = Depends(get_db)):
    version = db.get(R20PromptVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    return list(
        db.scalars(
            select(R20PromptValidationSuite)
            .where(R20PromptValidationSuite.prompt_version_id == version.id)
            .order_by(R20PromptValidationSuite.created_at.desc())
        )
    )


@router.post(
    "/prompt-versions/{version_id}/validation-suites",
    response_model=R20PromptValidationSuiteOut,
    status_code=201,
)
def create_prompt_validation_suite(
    version_id: str,
    payload: R20PromptValidationSuiteIn,
    db: Session = Depends(get_db),
):
    version = db.get(R20PromptVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if version.protocol_id != PROTOCOL_ID or version.status != "draft":
        raise HTTPException(status_code=409, detail="仅活动协议的草稿提示词可验证")
    active = db.scalar(
        select(R20PromptValidationSuite.id).where(
            R20PromptValidationSuite.prompt_version_id == version.id,
            R20PromptValidationSuite.status.in_(("queued", "running")),
        )
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="该提示词已有验证任务在运行")
    passed = db.scalar(
        select(R20PromptValidationSuite.id).where(
            R20PromptValidationSuite.prompt_version_id == version.id,
            R20PromptValidationSuite.protocol_id == PROTOCOL_ID,
            R20PromptValidationSuite.status == "passed",
        )
    )
    if passed is not None:
        raise HTTPException(status_code=409, detail="该提示词已有一个通过的验证任务")
    model_ids = sorted(payload.model_config_ids)
    models = [db.get(R20ModelConfig, model_id) for model_id in model_ids]
    if any(model is None or model.status != "frozen" for model in models):
        raise HTTPException(status_code=409, detail="验证要求两个已冻结模型配置")
    suite = R20PromptValidationSuite(
        id=str(uuid.uuid4()),
        protocol_id=PROTOCOL_ID,
        prompt_version_id=version.id,
        model_config_ids_json=model_ids,
        status="queued",
        expected_calls=EXPECTED_VALIDATION_CALLS,
        completed_calls=0,
    )
    db.add(suite)
    db.commit()
    db.refresh(suite)
    return suite


@router.delete("/prompt-versions/{version_id}", status_code=204)
def delete_prompt_version(version_id: str, db: Session = Depends(get_db)):
    row = db.get(R20PromptVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if any(
        db.scalars(select(R20Project.id).where(R20Project.prompt_version_id == row.id))
    ):
        raise HTTPException(status_code=409, detail="提示词版本已被项目引用")
    running = db.scalar(
        select(R20PromptValidationSuite.id).where(
            R20PromptValidationSuite.prompt_version_id == row.id,
            R20PromptValidationSuite.status.in_(("queued", "running")),
        )
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="该提示词版本有验证任务在运行")
    for trial in db.scalars(
        select(R20PromptTrial).where(R20PromptTrial.prompt_version_id == row.id)
    ):
        db.delete(trial)
    for suite in db.scalars(
        select(R20PromptValidationSuite).where(
            R20PromptValidationSuite.prompt_version_id == row.id
        )
    ):
        db.delete(suite)
    db.delete(row)
    db.commit()


@router.post("/prompt-versions/{version_id}/freeze", response_model=R20PromptVersionOut)
def freeze_prompt_version(version_id: str, db: Session = Depends(get_db)):
    row = db.get(R20PromptVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="仅草稿提示词可冻结")
    if row.protocol_id != PROTOCOL_ID:
        raise HTTPException(status_code=409, detail="历史 v1 提示词只读")
    row.status = "frozen"
    row.frozen_at = utc_now_naive()
    db.commit()
    return row


def _sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _analysis_code_sha() -> str:
    from app.experiment.r20 import analysis

    return hashlib.sha256(inspect.getsource(analysis).encode()).hexdigest()


def _project(db: Session, project_id: str) -> R20Project:
    row = db.get(R20Project, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="r20 项目不存在")
    return row


def _require_active_protocol(project: R20Project) -> None:
    if project.protocol_id != PROTOCOL_ID:
        raise HTTPException(
            status_code=409,
            detail="历史 v1 项目仅供审计，不能修改或继续执行",
        )


def _progress(db: Session, project: R20Project) -> dict:
    total = int(project.manifest_json.get("expected_calls", 0))
    counts = {
        status: count
        for status, count in db.execute(
            select(R20Call.status, func.count())
            .where(R20Call.project_id == project.id)
            .group_by(R20Call.status)
        )
    }
    return {"total": total, "recorded": sum(counts.values()), **counts}


def _out(db: Session, project: R20Project) -> dict:
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
        "manifest_json": (
            project.manifest_json
            if project.status not in {"running"}
            else {"protocol": project.protocol_id, "blinded": True}
        ),
        "progress": _progress(db, project),
        "created_at": project.created_at,
        "frozen_at": project.frozen_at,
        "started_at": project.started_at,
        "completed_at": project.completed_at,
    }


@router.get("/projects", response_model=list[R20ProjectOut])
def projects(db: Session = Depends(get_db)):
    return [
        _out(db, row)
        for row in db.scalars(select(R20Project).order_by(R20Project.created_at.desc()))
    ]


@router.get("/projects/{project_id}", response_model=R20ProjectOut)
def project(project_id: str, db: Session = Depends(get_db)):
    return _out(db, _project(db, project_id))


@router.post("/projects", response_model=R20ProjectOut, status_code=201)
def create_project(payload: R20ProjectCreate, db: Session = Depends(get_db)):
    if db.scalar(select(R20Project).where(R20Project.name == payload.name)):
        raise HTTPException(status_code=409, detail="项目名称已存在")
    models = [db.get(R20ModelConfig, model_id) for model_id in payload.model_config_ids]
    if any(model is None or model.status != "frozen" for model in models):
        raise HTTPException(status_code=409, detail="两个 r20 模型配置都必须已冻结")
    models = sorted(models, key=lambda model: model.config_json["requested_model"])
    prompt = db.get(R20PromptVersion, payload.prompt_version_id)
    if prompt is None or prompt.status != "frozen" or prompt.protocol_id != PROTOCOL_ID:
        raise HTTPException(status_code=409, detail="必须使用已冻结的活动协议提示词")
    configs = [model.config_json for model in models]
    model_hash = _sha(configs)
    signature = (
        _sha(
            {
                "protocol": PROTOCOL_ID,
                "models": model_hash,
                "prompt": prompt.templates_sha256,
            }
        )
        if payload.kind.value == "formal"
        else None
    )
    row = R20Project(
        id=str(uuid.uuid4()),
        protocol_id=PROTOCOL_ID,
        name=payload.name,
        kind=payload.kind.value,
        status="draft",
        model_config_ids_json=[model.id for model in models],
        model_configs_json=configs,
        model_configs_sha256=model_hash,
        prompt_version_id=prompt.id,
        prompt_version_name=prompt.name,
        prompt_templates_json=prompt.templates_json,
        prompt_version_sha256=prompt.templates_sha256,
        pilot_project_id=payload.pilot_project_id,
        formal_signature=signature,
        manifest_json={"protocol": PROTOCOL_ID},
        manifest_sha256="0" * 64,
        data_sha256="0" * 64,
        analysis_code_sha256=_analysis_code_sha(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="该协议配置已存在 formal 项目"
        ) from exc
    return _out(db, row)


def _selected(dataset, project: R20Project):
    role = "formal" if project.kind == "formal" else "pilot_run"
    return sorted(
        question for question, value in dataset.roles.items() if value == role
    )


def _balanced_order(model_ids: list[str], question_index: int):
    """Rotate one frozen base permutation; 18 questions cover every position."""
    base = sorted(
        (
            (model, trajectory, condition)
            for model in sorted(model_ids)
            for trajectory in (1, 2, 3)
            for condition in ("nm", "crm", "arm")
        ),
        key=lambda value: hashlib.sha256(
            f"r20-order-base:{value}".encode()
        ).hexdigest(),
    )
    offset = question_index % len(base)
    return base[offset:] + base[:offset]


@router.post("/projects/{project_id}/freeze", response_model=R20ProjectOut)
def freeze_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status != "draft":
        raise HTTPException(status_code=409, detail="仅草稿项目可冻结")
    dataset = build_frozen_dataset(
        settings.R20_SAF_ARCHIVE_PATH, settings.R20_SAF_SPLIT_MAP_PATH
    )
    selected = _selected(dataset, project)
    schedule = schedule_for_kind(project.kind)
    expected = 0
    by_id = {
        row.answer_id: row for row in dataset.records if row.excluded_reason is None
    }
    order_manifest = {}
    for question_index, question in enumerate(selected):
        tests_by_id = {
            row.answer_id: row
            for row in dataset.records_for(question, "test_unseen_answers")
        }
        tests = [
            tests_by_id[answer_id] for answer_id in dataset.test_endpoints[question]
        ]
        expected += expected_question_calls(
            schedule.memory_count,
            len(tests),
            len(schedule.probe_checkpoints),
        )
        for position, row in enumerate(tests):
            db.add(
                R20Record(
                    project_id=project.id,
                    answer_id=row.answer_id,
                    group_id=row.group_id,
                    question_id=question,
                    question_text=row.question_text,
                    reference_answer=row.reference_answer,
                    student_answer=row.student_answer,
                    teacher_score=row.teacher_score,
                    teacher_feedback=row.teacher_feedback,
                    max_score=dataset.max_scores[question],
                    source_split=row.source_split,
                    usage="test",
                    trajectory=0,
                    position=position,
                    probe=row.answer_id in dataset.probes[question],
                )
            )
        for trajectory, ids in dataset.trajectories[question].items():
            for position, answer_id in enumerate(ids, start=1):
                row = by_id[answer_id]
                db.add(
                    R20Record(
                        project_id=project.id,
                        answer_id=row.answer_id,
                        group_id=row.group_id,
                        question_id=question,
                        question_text=row.question_text,
                        reference_answer=row.reference_answer,
                        student_answer=row.student_answer,
                        teacher_score=row.teacher_score,
                        teacher_feedback=row.teacher_feedback,
                        max_score=dataset.max_scores[question],
                        source_split=row.source_split,
                        usage="memory",
                        trajectory=int(trajectory),
                        position=position,
                        probe=False,
                    )
                )
        model_ids = [config["requested_model"] for config in project.model_configs_json]
        order = _balanced_order(model_ids, question_index)
        order_manifest[question] = [
            {
                "model_id": model,
                "trajectory": trajectory,
                "condition": condition,
            }
            for model, trajectory, condition in order
        ]
        for rank, (model, trajectory, condition) in enumerate(order):
            db.add(
                R20Stream(
                    project_id=project.id,
                    model_id=model,
                    question_id=question,
                    condition=condition,
                    trajectory=trajectory,
                    order_rank=rank,
                    status="pending",
                )
            )
    db.flush()
    model_ids = [config["requested_model"] for config in project.model_configs_json]
    for question_index, question in enumerate(selected):
        for condition_index, condition in enumerate(("nm", "crm", "arm")):
            group_expected = sum(
                len(
                    call_sequence(
                        db,
                        project,
                        R20Stream(
                            project_id=project.id,
                            model_id=model,
                            question_id=question,
                            condition=condition,
                            trajectory=trajectory,
                            order_rank=0,
                            status="pending",
                        ),
                    )
                )
                for model in model_ids
                for trajectory in (1, 2, 3)
            )
            db.add(
                R20RunGroup(
                    project_id=project.id,
                    question_id=question,
                    condition=condition,
                    status="pending",
                    order_rank=question_index * 3 + condition_index,
                    expected_calls=group_expected,
                )
            )
    frozen_manifest = {
        **dataset.manifest,
        "selected_questions": selected,
        "schedule": schedule.as_dict(),
        "expected_calls": expected,
        "model_configs_sha256": project.model_configs_sha256,
        "prompt_version_sha256": project.prompt_version_sha256,
        "analysis_code_sha256": project.analysis_code_sha256,
        "call_order_algorithm": "fixed-base-cyclic-rotation-r20-v1",
        "call_order": order_manifest,
    }
    project.manifest_json = frozen_manifest
    project.manifest_sha256 = _sha(frozen_manifest)
    project.data_sha256 = dataset.manifest["manifest_sha256"]
    project.status = "frozen"
    project.frozen_at = utc_now_naive()
    db.commit()
    return _out(db, project)


def _group_out(db: Session, group: R20RunGroup) -> dict:
    counts = {
        status: count
        for status, count in db.execute(
            select(R20Call.status, func.count())
            .where(
                R20Call.project_id == group.project_id,
                R20Call.question_id == group.question_id,
                R20Call.condition == group.condition,
            )
            .group_by(R20Call.status)
        )
    }
    return {
        "id": group.id,
        "project_id": group.project_id,
        "question_id": group.question_id,
        "condition": group.condition,
        "status": group.status,
        "order_rank": group.order_rank,
        "expected_calls": group.expected_calls,
        "progress": {"recorded": sum(counts.values()), **counts},
        "started_at": group.started_at,
        "completed_at": group.completed_at,
    }


def _restore_missing_run_groups(db: Session, project: R20Project) -> bool:
    """Backfill grid cells for projects frozen before run groups were introduced.

    A frozen project already has immutable records and streams, so rebuilding the
    lightweight group rows does not alter its dataset, prompts, call order, or
    any executable calls.  This makes old frozen projects operable without
    requiring users to recreate and re-freeze them.
    """
    if project.status not in {"frozen", "running", "paused"}:
        return False
    existing = list(
        db.scalars(select(R20RunGroup).where(R20RunGroup.project_id == project.id))
    )
    if existing:
        return False
    streams = list(
        db.scalars(
            select(R20Stream)
            .where(R20Stream.project_id == project.id)
            .order_by(R20Stream.question_id, R20Stream.condition, R20Stream.order_rank)
        )
    )
    if not streams:
        return False
    stream_questions = {stream.question_id for stream in streams}
    frozen_questions = project.manifest_json.get("selected_questions", [])
    questions = [
        question for question in frozen_questions if question in stream_questions
    ]
    questions.extend(sorted(stream_questions - set(questions)))
    by_cell: dict[tuple[str, str], list[R20Stream]] = {}
    for stream in streams:
        by_cell.setdefault((stream.question_id, stream.condition), []).append(stream)
    for question_index, question in enumerate(questions):
        for condition_index, condition in enumerate(("nm", "crm", "arm")):
            cell_streams = by_cell.get((question, condition), [])
            if not cell_streams:
                continue
            db.add(
                R20RunGroup(
                    project_id=project.id,
                    question_id=question,
                    condition=condition,
                    status="pending",
                    order_rank=question_index * 3 + condition_index,
                    expected_calls=sum(
                        len(call_sequence(db, project, stream))
                        for stream in cell_streams
                    ),
                )
            )
    db.flush()
    return True


@router.get("/projects/{project_id}/groups", response_model=list[R20RunGroupOut])
def run_groups(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    if project.protocol_id == PROTOCOL_ID and _restore_missing_run_groups(db, project):
        db.commit()
    return [
        _group_out(db, row)
        for row in db.scalars(
            select(R20RunGroup)
            .where(R20RunGroup.project_id == project.id)
            .order_by(R20RunGroup.order_rank)
        )
    ]


@router.post(
    "/projects/{project_id}/groups/{question_id}/{condition}/start",
    response_model=R20RunGroupOut,
)
def start_run_group(
    project_id: str, question_id: str, condition: str, db: Session = Depends(get_db)
):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status not in {"frozen", "running"}:
        raise HTTPException(status_code=409, detail="当前状态不能启动实验组")
    if condition not in ("nm", "crm", "arm"):
        raise HTTPException(status_code=404, detail="实验组不存在")
    group = db.scalar(
        select(R20RunGroup).where(
            R20RunGroup.project_id == project.id,
            R20RunGroup.question_id == question_id,
            R20RunGroup.condition == condition,
        )
    )
    if group is None:
        raise HTTPException(status_code=404, detail="实验组不存在")
    if group.status != "pending":
        raise HTTPException(status_code=409, detail="实验组已启动")
    active = db.scalar(
        select(R20RunGroup.id).where(
            R20RunGroup.project_id == project.id,
            R20RunGroup.status.in_(("queued", "running")),
        )
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="已有实验组在运行，请等待其完成")
    if project.status == "frozen":
        if (
            build_frozen_dataset(
                settings.R20_SAF_ARCHIVE_PATH, settings.R20_SAF_SPLIT_MAP_PATH
            ).manifest["manifest_sha256"]
            != project.data_sha256
        ):
            raise HTTPException(status_code=409, detail="冻结数据哈希已变化")
        if _analysis_code_sha() != project.analysis_code_sha256:
            raise HTTPException(status_code=409, detail="分析代码哈希已变化")
    group.status = "queued"
    group.started_at = utc_now_naive()
    project.status = "running"
    project.started_at = project.started_at or utc_now_naive()
    db.add(
        R20Exposure(
            project_id=project.id,
            question_id=question_id,
            action="start_group",
            detail_json={"condition": condition},
        )
    )
    db.commit()
    return _group_out(db, group)


@router.post("/projects/{project_id}/terminate", response_model=R20ProjectOut)
def terminate_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status not in {"draft", "frozen", "running"}:
        raise HTTPException(status_code=409, detail="当前状态不能终止")
    project.status = "terminated"
    project.terminated_at = utc_now_naive()
    db.commit()
    return _out(db, project)


@router.post("/projects/{project_id}/pause", response_model=R20ProjectOut)
def pause_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status != "running":
        raise HTTPException(status_code=409, detail="仅运行中的项目可暂停")
    project.status = "paused"
    db.add(
        R20Exposure(
            project_id=project.id,
            question_id="",
            action="pause",
            detail_json={"from": "running"},
        )
    )
    db.commit()
    return _out(db, project)


@router.post("/projects/{project_id}/resume", response_model=R20ProjectOut)
def resume_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status != "paused":
        raise HTTPException(status_code=409, detail="仅已暂停的项目可恢复")
    project.status = "running"
    db.add(
        R20Exposure(
            project_id=project.id,
            question_id="",
            action="resume",
            detail_json={"from": "paused"},
        )
    )
    db.commit()
    return _out(db, project)


def _wait_for_inflight_to_drain(db: Session, project_id: str) -> None:
    """Wait for leased streams so an in-flight call finishes before row deletion.

    Deleting calls while a worker is still saving an attempt would violate the
    ``r20_call_attempts_call_id_fkey`` constraint and crash the worker, so the
    worker must first release its leases.  Bounded wait; the client can retry.
    """
    deadline = time.monotonic() + DELETE_DRAIN_TIMEOUT_SECONDS
    while (
        db.scalar(
            select(R20Stream.id).where(
                R20Stream.project_id == project_id,
                R20Stream.status == "leased",
            )
        )
        is not None
    ):
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=409,
                detail="项目仍有进行中的调用，请稍后重试",
            )
        db.rollback()
        time.sleep(DELETE_DRAIN_POLL_SECONDS)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status in {"running"}:
        project.status = "terminated"
        project.terminated_at = utc_now_naive()
        db.commit()
    _wait_for_inflight_to_drain(db, project.id)
    attempts = db.scalars(
        select(R20CallAttempt).join(R20Call).where(R20Call.project_id == project.id)
    )
    for attempt in attempts:
        db.delete(attempt)
    for call in db.scalars(select(R20Call).where(R20Call.project_id == project.id)):
        db.delete(call)
    for snapshot in db.scalars(
        select(R20Snapshot).where(R20Snapshot.project_id == project.id)
    ):
        db.delete(snapshot)
    for stream in db.scalars(
        select(R20Stream).where(R20Stream.project_id == project.id)
    ):
        db.delete(stream)
    for group in db.scalars(
        select(R20RunGroup).where(R20RunGroup.project_id == project.id)
    ):
        db.delete(group)
    for record in db.scalars(
        select(R20Record).where(R20Record.project_id == project.id)
    ):
        db.delete(record)
    for report in db.scalars(
        select(R20Report).where(R20Report.project_id == project.id)
    ):
        db.delete(report)
    for exposure in db.scalars(
        select(R20Exposure).where(R20Exposure.project_id == project.id)
    ):
        db.delete(exposure)
    db.delete(project)
    db.commit()


@router.get("/projects/{project_id}/report", response_model=R20ReportOut)
def report(project_id: str, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    saved = db.scalar(select(R20Report).where(R20Report.project_id == project.id))
    if saved is None:
        return {
            "status": project.status,
            "project_id": project.id,
            "reason": "报告仅在全部实验组完成时自动生成并锁定",
        }
    return {
        "status": project.status,
        "project_id": project.id,
        "report": saved.report_json,
        "report_sha256": saved.report_sha256,
    }


@router.get("/projects/{project_id}/calls", response_model=R20CallPageOut)
def calls(
    project_id: str,
    limit: int = Query(200, ge=1, le=200),
    db: Session = Depends(get_db),
):
    project = _project(db, project_id)
    if project.kind != "formal" or project.status != "completed":
        raise HTTPException(
            status_code=403,
            detail="调用内容仅在正式实验成功完成并锁定报告后可读",
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
        "total": int(
            db.scalar(
                select(func.count())
                .select_from(R20Call)
                .where(R20Call.project_id == project.id)
            )
            or 0
        ),
        "summary": _progress(db, project),
    }


@router.get("/projects/{project_id}/failures", response_model=R20CallPageOut)
def failures(project_id: str, db: Session = Depends(get_db)):
    """Blinded failure list: metadata and reason only, never inputs or teacher data."""
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
        "summary": _progress(db, project),
    }


@router.post(
    "/projects/{project_id}/calls/{call_id}/retry", response_model=R20ProjectOut
)
def retry_call(project_id: str, call_id: int, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    _require_active_protocol(project)
    if project.status not in {"running", "paused", "completed_with_failures"}:
        raise HTTPException(
            status_code=409, detail="仅运行中、已暂停或含失败完成的项目可重试调用"
        )
    call = db.get(R20Call, call_id)
    if call is None or call.project_id != project.id:
        raise HTTPException(status_code=404, detail="r20 调用不存在")
    if call.status == "retry_pending":
        # Already enqueued: the worker will retry it in serial order. Treat a
        # repeated manual click as an idempotent success, never a duplicate.
        return _out(db, project)
    if call.status != "failed_terminal":
        raise HTTPException(status_code=409, detail="仅失败调用可重试")
    stream = db.scalar(
        select(R20Stream)
        .where(
            R20Stream.project_id == project.id,
            R20Stream.model_id == call.model_id,
            R20Stream.question_id == call.question_id,
            R20Stream.condition == call.condition,
            R20Stream.trajectory == call.trajectory,
        )
        .with_for_update()
    )
    if stream is None:
        raise HTTPException(status_code=409, detail="调用所属执行流不存在")
    existing_report = db.scalar(
        select(R20Report).where(R20Report.project_id == project.id)
    )
    if existing_report is not None:
        db.delete(existing_report)
        project.report_sha256 = None
    call.status = "retry_pending"
    call.failure_reason = None
    call.retry_count = 0
    call.next_retry_at = None
    if (
        stream.status == "leased"
        and stream.lease_until is not None
        and stream.lease_until > utc_now_naive()
    ):
        # The worker currently owns the stream (e.g. mid-attempt on another
        # call). Leave the stream row untouched: when the worker finishes it
        # releases the lease and the next _lease_retry picks this call up in
        # serial order, so manual and automatic retries never compete.
        pass
    else:
        stream.status, stream.worker_id, stream.lease_until = "pending", None, None
    if project.status == "completed_with_failures":
        project.status = "running"
        project.completed_at = None
        group = db.scalar(
            select(R20RunGroup).where(
                R20RunGroup.project_id == project.id,
                R20RunGroup.question_id == call.question_id,
                R20RunGroup.condition == call.condition,
            )
        )
        if group is not None and group.status == "completed":
            group.status = "queued"
            group.completed_at = None
    db.add(
        R20Exposure(
            project_id=project.id,
            question_id=call.question_id,
            action="manual_retry",
            detail_json={"call_id": call.id},
        )
    )
    db.commit()
    return _out(db, project)
