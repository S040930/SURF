"""r22 schemas and immutable token-budget configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.experiment.r20.tokenization import token_count
from app.experiment.r21.protocol import (
    ANSWER_COPY_TOKENS,
    MAX_CORE_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_ITEMS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
)
from app.experiment.r22 import COMPRESSION_VERSION

MAX_FEEDBACK_TOKENS = 180
# This is a transport safety bound, not an experimental token budget.  It is
# deliberately larger than the final feedback budget so a complete candidate
# can reach the recovery layer.
MAX_TRANSPORT_TEXT_CHARS = 12000

PROMPT_ENVELOPE_VERSION = "r22-codex-exec-v1"
TRAJECTORIES = (1, 2, 3)
CONDITIONS = ("nm", "crm", "arm")
TRAIN_REPEATS = (1,)
TEST_REPEATS = (1, 2)


class ScoreTransportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=3.5)
    feedback: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)


class ScoreOutput(ScoreTransportOutput):
    @field_validator("feedback")
    @classmethod
    def feedback_is_compact(cls, value: str) -> str:
        if token_count(value) > MAX_FEEDBACK_TOKENS:
            raise ValueError(f"feedback exceeds {MAX_FEEDBACK_TOKENS} tokens")
        return value


class CompactRuleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)
    effect: Literal["supports", "weakens"]
    importance: Literal["major", "moderate", "minor"]
    guidance: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)
    support: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)


class RubricAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sufficient: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)
    partial: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)
    missing: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)


class AbstractRubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)
    importance: Literal["major", "moderate", "minor"]
    anchors: RubricAnchors
    support: str = Field(min_length=1, max_length=MAX_TRANSPORT_TEXT_CHARS)


class CRMTransportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[CompactRuleItem] = Field(max_length=MAX_ITEMS)


class ARMTransportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric: list[AbstractRubricItem] = Field(max_length=MAX_ITEMS)


# Memory semantic validation is shared with r21 for now because the frozen
# rules are intentionally identical.  The transport schemas above are the
# only r22-specific relaxation: they do not reject token-overlong text.
CRMUpdateOutput = CRMTransportOutput
ARMUpdateOutput = ARMTransportOutput


@dataclass(frozen=True, slots=True)
class RunSchedule:
    memory_count: int = 40
    test_count: int = 10
    checkpoints: tuple[int, ...] = (10, 20, 30, 40)
    final_history: int = 40

    def as_dict(self) -> dict:
        return {
            "memory_count": self.memory_count,
            "test_count": self.test_count,
            "checkpoints": list(self.checkpoints),
            "final_history": self.final_history,
        }


PILOT_SCHEDULE = RunSchedule()


def transport_schema(kind: str, condition: str | None):
    if kind == "test_score":
        return ScoreTransportOutput
    if kind == "memory_update" and condition == "crm":
        return CRMTransportOutput
    if kind == "memory_update" and condition == "arm":
        return ARMTransportOutput
    raise ValueError(f"unknown r22 call {kind}/{condition}")


def output_schema(kind: str, condition: str | None):
    if kind == "test_score":
        return ScoreOutput
    if kind == "memory_update" and condition == "crm":
        return CRMUpdateOutput
    if kind == "memory_update" and condition == "arm":
        return ARMUpdateOutput
    raise ValueError(f"unknown r22 call {kind}/{condition}")


def expected_question_calls(schedule: RunSchedule = PILOT_SCHEDULE) -> int:
    """Primary calls per question, excluding optional compression calls."""
    memory = schedule.memory_count * len(TRAJECTORIES) * 2
    train_scores = schedule.memory_count * len(TRAJECTORIES) * len(CONDITIONS)
    test_scores = (
        schedule.test_count
        * len(schedule.checkpoints)
        * len(TRAJECTORIES)
        * len(CONDITIONS)
        * len(TEST_REPEATS)
    )
    return memory + train_scores + test_scores


__all__ = [
    "ANSWER_COPY_TOKENS",
    "ARMTransportOutput",
    "ARMUpdateOutput",
    "AbstractRubricItem",
    "COMPRESSION_VERSION",
    "CompactRuleItem",
    "CRMTransportOutput",
    "CRMUpdateOutput",
    "MAX_CORE_TOKENS",
    "MAX_FEEDBACK_TOKENS",
    "MAX_GUIDANCE_TOKENS",
    "MAX_ITEMS",
    "MAX_STORED_TOKENS",
    "MAX_SUPPORT_TOKENS",
    "MAX_TRANSPORT_TEXT_CHARS",
    "MAX_VISIBLE_TOKENS",
    "CONDITIONS",
    "PILOT_SCHEDULE",
    "PROMPT_ENVELOPE_VERSION",
    "RubricAnchors",
    "RunSchedule",
    "ScoreOutput",
    "ScoreTransportOutput",
    "output_schema",
    "transport_schema",
    "TEST_REPEATS",
    "TRAIN_REPEATS",
    "TRAJECTORIES",
    "expected_question_calls",
]
