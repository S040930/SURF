"""Durable, outcome-blind 48-call validation for r20 v4 prompts."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.gateway import complete_request
from app.experiment.r20.dataset import build_frozen_dataset
from app.experiment.r20.memory import parse_memory_update, render_memory
from app.experiment.r20.protocol import NO_MEMORY_TEXT, PromptTemplates
from app.experiment.r20.runner import make_chat_request, score_messages, update_messages
from app.experiment.types import ModelConfig
from app.models.r20 import (
    R20ModelConfig,
    R20PromptTrial,
    R20PromptValidationSuite,
    R20PromptVersion,
)

EXPECTED_VALIDATION_CALLS = 48


@dataclass(frozen=True, slots=True)
class ValidationSpec:
    model_config_id: str
    question_id: str
    condition: str
    template_kind: str
    scenario: str
    answer_id: str


def _middle(rows, max_score: float):
    return min(
        rows, key=lambda row: (abs(row.teacher_score / max_score - 0.5), row.answer_id)
    )


def validation_specs(dataset, model_config_ids: list[str]) -> list[ValidationSpec]:
    specs: list[ValidationSpec] = []
    questions = sorted(
        question_id
        for question_id, role in dataset.roles.items()
        if role == "development"
    )
    if len(questions) != 2:
        raise ValueError("r20 v4 requires exactly two development questions")
    for model_id in sorted(model_config_ids):
        for question_id in questions:
            train = dataset.records_for(question_id, "train")
            validation = dataset.records_for(question_id, "validation")
            max_score = max(row.teacher_score for row in train)
            high = min(train, key=lambda row: (-row.teacher_score, row.answer_id))
            low = min(train, key=lambda row: (row.teacher_score, row.answer_id))
            middle = _middle(train, max_score)
            if len({high.teacher_score, low.teacher_score, middle.teacher_score}) != 3:
                raise ValueError(
                    f"development question {question_id} lacks three score strata"
                )
            for condition in ("crm", "arm"):
                for scenario, row in (
                    ("train_high", high),
                    ("train_low", low),
                    ("train_middle", middle),
                ):
                    specs.append(
                        ValidationSpec(
                            model_id,
                            question_id,
                            condition,
                            f"{condition}_update",
                            scenario,
                            row.answer_id,
                        )
                    )
            held_low = min(
                validation, key=lambda row: (row.teacher_score, row.answer_id)
            )
            held_high = min(
                validation, key=lambda row: (-row.teacher_score, row.answer_id)
            )
            for condition in ("nm", "crm", "arm"):
                for scenario, row in (
                    ("validation_low", held_low),
                    ("validation_high", held_high),
                ):
                    specs.append(
                        ValidationSpec(
                            model_id,
                            question_id,
                            condition,
                            "scoring",
                            scenario,
                            row.answer_id,
                        )
                    )
    if len(specs) != EXPECTED_VALIDATION_CALLS:
        raise AssertionError(f"expected 48 validation calls, got {len(specs)}")
    return specs


def _prior_items(
    db: Session, suite_id: str, model_id: str, question_id: str, condition: str
) -> list[dict]:
    trial = db.scalar(
        select(R20PromptTrial)
        .where(
            R20PromptTrial.suite_id == suite_id,
            R20PromptTrial.model_config_id == model_id,
            R20PromptTrial.question_id == question_id,
            R20PromptTrial.condition == condition,
            R20PromptTrial.template_kind == f"{condition}_update",
            R20PromptTrial.status == "success",
        )
        .order_by(R20PromptTrial.order_index.desc())
        .limit(1)
    )
    if trial is None or trial.output_json is None:
        return []
    key = "rules" if condition == "crm" else "rubric"
    return list(trial.output_json[key])


def _row(dataset, answer_id: str):
    return next(row for row in dataset.records if row.answer_id == answer_id)


def process_validation_call(db: Session, suite: R20PromptValidationSuite) -> None:
    dataset = _validation_dataset(
        str(settings.R20_SAF_ARCHIVE_PATH), str(settings.R20_SAF_SPLIT_MAP_PATH)
    )
    specs = validation_specs(dataset, list(suite.model_config_ids_json))
    order_index = suite.completed_calls
    pending = db.scalar(
        select(R20PromptTrial).where(
            R20PromptTrial.suite_id == suite.id,
            R20PromptTrial.order_index == order_index,
            R20PromptTrial.status == "pending",
        )
    )
    if pending is not None:
        pending.status = "uncertain"
        pending.error = (
            "validation request outcome became ambiguous after lease recovery"
        )
        suite.status = "failed"
        suite.failure_reason = pending.error
        suite.completed_at = utc_now_naive()
        suite.worker_id = None
        suite.lease_until = None
        db.commit()
        return
    if order_index >= len(specs):
        suite.status = "passed"
        suite.completed_at = utc_now_naive()
        suite.worker_id = None
        suite.lease_until = None
        db.commit()
        return

    spec = specs[order_index]
    version = db.get(R20PromptVersion, suite.prompt_version_id)
    model_row = db.get(R20ModelConfig, spec.model_config_id)
    if version is None or model_row is None:
        raise ValueError("validation suite configuration no longer exists")
    templates = PromptTemplates.model_validate(version.templates_json)
    model = ModelConfig.model_validate(model_row.config_json)
    row = _row(dataset, spec.answer_id)
    max_score = max(
        item.teacher_score for item in dataset.records_for(spec.question_id, "train")
    )

    if spec.template_kind == "scoring":
        items = (
            []
            if spec.condition == "nm"
            else _prior_items(
                db, suite.id, spec.model_config_id, spec.question_id, spec.condition
            )
        )
        if spec.condition != "nm" and not items:
            raise ValueError("memory scoring validation requires a completed snapshot")
        memory = (
            NO_MEMORY_TEXT
            if spec.condition == "nm"
            else render_memory(spec.condition, items)
        )
        messages = score_messages(
            templates.scoring,
            row.question_text,
            row.reference_answer,
            row.student_answer,
            max_score,
            memory,
        )
        request = make_chat_request(
            model,
            messages,
            "test_score",
            spec.condition,
            lambda parsed: _validate_score(parsed, max_score),
        )
        input_json = {
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "max_score": max_score,
            "memory": memory,
        }
    else:
        items = _prior_items(
            db, suite.id, spec.model_config_id, spec.question_id, spec.condition
        )
        instruction = (
            templates.crm_update if spec.condition == "crm" else templates.arm_update
        )
        messages = update_messages(
            instruction,
            row.question_text,
            row.reference_answer,
            row.student_answer,
            max_score,
            row.teacher_score,
            row.teacher_feedback,
            items,
        )
        request = make_chat_request(
            model,
            messages,
            "memory_update",
            spec.condition,
            lambda parsed: parse_memory_update(
                spec.condition,
                parsed.model_dump(mode="json"),
                row.student_answer,
                row.question_text,
                row.reference_answer,
            ),
        )
        input_json = {
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "max_score": max_score,
            "teacher_score": row.teacher_score,
            "teacher_feedback": row.teacher_feedback,
            "existing_memory": items,
        }

    trial = R20PromptTrial(
        id=str(uuid.uuid4()),
        suite_id=suite.id,
        prompt_version_id=version.id,
        model_config_id=model_row.id,
        template_kind=spec.template_kind,
        condition=spec.condition,
        scenario=spec.scenario,
        order_index=order_index,
        status="pending",
        question_id=spec.question_id,
        answer_id=spec.answer_id,
        input_json=input_json,
        output_json=None,
        valid=False,
    )
    db.add(trial)
    db.commit()

    try:
        result = asyncio.run(complete_request(request))
        trial.output_json = result.parsed.model_dump(mode="json")
        trial.valid = True
        trial.status = "success"
        suite.completed_calls += 1
        if suite.completed_calls == suite.expected_calls:
            suite.status = "passed"
            suite.completed_at = utc_now_naive()
    except Exception as exc:
        trial.status = "failed"
        trial.error = str(exc)
        suite.status = "failed"
        suite.failure_reason = str(exc)
        suite.completed_at = utc_now_naive()
    suite.worker_id = None
    suite.lease_until = None
    db.commit()


def _validate_score(parsed, max_score: float) -> None:
    if parsed.score > max_score:
        raise ValueError("score exceeds maximum")


@lru_cache(maxsize=1)
def _validation_dataset(archive_path: str, split_map_path: str):
    return build_frozen_dataset(archive_path, split_map_path)
