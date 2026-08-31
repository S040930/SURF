"""Frozen types and limits for the r20 SAF experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TRAJECTORIES = (1, 2, 3)
PROBE_TEST_COUNT = 5
FINAL_REPEATS = (1, 2)
MAX_ITEMS = 6
# Frozen by docs/experiment/r20-token-calibration.json: development corpus p95
# token/legacy-word ratio = 119/81, rounded upward for each former word budget.
MAX_VISIBLE_TOKENS = 441
MAX_STORED_TOKENS = 618
MAX_CORE_TOKENS = 30
MAX_GUIDANCE_TOKENS = 42
MAX_SUPPORT_TOKENS = 30
MAX_FEEDBACK_TOKENS = 118
ANSWER_COPY_TOKENS = 18
IMPORTANCE = ("major", "moderate", "minor")
NO_MEMORY_TEXT = "No learned memory is available for this question."


@dataclass(frozen=True, slots=True)
class RunSchedule:
    memory_count: int
    checkpoints: tuple[int, ...]
    probe_checkpoints: tuple[int, ...]
    test_count: int
    final_history: int

    def as_dict(self) -> dict:
        value = asdict(self)
        value["checkpoints"] = list(self.checkpoints)
        value["probe_checkpoints"] = list(self.probe_checkpoints)
        return value


FORMAL_SCHEDULE = RunSchedule(
    memory_count=60,
    checkpoints=(20, 40, 60),
    probe_checkpoints=(20, 40),
    test_count=15,
    final_history=60,
)
PILOT_SCHEDULE = RunSchedule(
    memory_count=20,
    checkpoints=(10, 20),
    probe_checkpoints=(10,),
    test_count=5,
    final_history=20,
)


def schedule_for_kind(kind: str) -> RunSchedule:
    if kind == "formal":
        return FORMAL_SCHEDULE
    if kind == "pilot_run":
        return PILOT_SCHEDULE
    raise ValueError(f"unknown r20 project kind: {kind}")


def schedule_from_manifest(manifest: dict, kind: str) -> RunSchedule:
    raw = manifest.get("schedule")
    if raw is None:
        return schedule_for_kind(kind)
    return RunSchedule(
        memory_count=int(raw["memory_count"]),
        checkpoints=tuple(int(value) for value in raw["checkpoints"]),
        probe_checkpoints=tuple(int(value) for value in raw["probe_checkpoints"]),
        test_count=int(raw["test_count"]),
        final_history=int(raw["final_history"]),
    )


class ProjectKind(StrEnum):
    pilot_run = "pilot_run"
    formal = "formal"


class ProjectStatus(StrEnum):
    draft = "draft"
    frozen = "frozen"
    queued = "queued"
    running = "running"
    completed = "completed"
    completed_with_failures = "completed_with_failures"
    terminated = "terminated"


class Condition(StrEnum):
    nm = "nm"
    crm = "crm"
    arm = "arm"


class CallKind(StrEnum):
    memory_update = "memory_update"
    test_score = "test_score"


class PromptTemplates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scoring: str = Field(min_length=1, max_length=12000)
    crm_update: str = Field(min_length=1, max_length=12000)
    arm_update: str = Field(min_length=1, max_length=12000)


class ScoreOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=3.5)
    feedback: str = Field(min_length=1, max_length=800)

    @field_validator("feedback")
    @classmethod
    def feedback_is_compact(cls, value: str) -> str:
        from app.experiment.r20.tokenization import token_count

        if token_count(value) > MAX_FEEDBACK_TOKENS:
            raise ValueError(f"feedback exceeds {MAX_FEEDBACK_TOKENS} tokens")
        return value


class CompactRuleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1, max_length=320)
    effect: Literal["supports", "weakens"]
    importance: Literal["major", "moderate", "minor"]
    guidance: str = Field(min_length=1, max_length=480)
    support: str = Field(min_length=1, max_length=320)


class RubricAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sufficient: str = Field(min_length=1, max_length=480)
    partial: str = Field(min_length=1, max_length=480)
    missing: str = Field(min_length=1, max_length=480)


class AbstractRubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=320)
    importance: Literal["major", "moderate", "minor"]
    anchors: RubricAnchors
    support: str = Field(min_length=1, max_length=320)


class CRMUpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[CompactRuleItem] = Field(max_length=MAX_ITEMS)


class ARMUpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric: list[AbstractRubricItem] = Field(max_length=MAX_ITEMS)
