"""r20 request builders: one instruction message and one JSON data message."""

from __future__ import annotations

import json
from typing import Any

from app.experiment.gateway import ChatRequest
from app.experiment.r20.protocol import ARMUpdateOutput, CRMUpdateOutput, ScoreOutput
from app.experiment.types import ModelConfig


def _messages(instruction: str, payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    return (
        {"role": "system", "content": instruction},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )


def score_messages(
    instruction: str,
    question: str,
    reference_answer: str,
    answer: str,
    max_score: float,
    memory: str,
):
    return _messages(
        instruction,
        {
            "question": question,
            "reference_answer": reference_answer,
            "answer": answer,
            "max_score": max_score,
            "memory": memory,
        },
    )


def update_messages(
    instruction: str,
    question: str,
    reference_answer: str,
    answer: str,
    max_score: float,
    teacher_score: float,
    teacher_feedback: str,
    existing_memory: list[dict],
):
    return _messages(
        instruction,
        {
            "question": question,
            "reference_answer": reference_answer,
            "answer": answer,
            "max_score": max_score,
            "teacher_score": teacher_score,
            "teacher_feedback": teacher_feedback,
            "existing_memory": existing_memory,
        },
    )


def make_chat_request(
    model: ModelConfig,
    messages,
    kind: str,
    condition: str | None = None,
    result_validator=None,
) -> ChatRequest:
    if kind == "test_score":
        schema = ScoreOutput
    elif condition == "crm":
        schema = CRMUpdateOutput
    elif condition == "arm":
        schema = ARMUpdateOutput
    else:
        raise ValueError(f"unknown r20 call {kind}/{condition}")
    return ChatRequest(
        model=model,
        messages=messages,
        output_schema=schema,
        result_validator=result_validator,
        schema_prefix="r20",
    )
