"""Frozen r21 schedule and runner configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.experiment.r20.tokenization import token_count

TRAJECTORIES = (1, 2, 3)
PROBE_TEST_COUNT = 5
FINAL_REPEATS = (1, 2)
PROMPT_ENVELOPE_VERSION = "r21-codex-exec-v1"

# r21 uses a deliberately roomier, independently frozen token calibration than
# r20.  These values are part of the r21 prompt envelope and must not be changed
# for an already-frozen project.
MAX_ITEMS = 6
MAX_VISIBLE_TOKENS = 720
MAX_STORED_TOKENS = 960
MAX_CORE_TOKENS = 48
MAX_GUIDANCE_TOKENS = 72
MAX_SUPPORT_TOKENS = 48
MAX_FEEDBACK_TOKENS = 180
ANSWER_COPY_TOKENS = 18


@dataclass(frozen=True, slots=True)
class RunSchedule:
    memory_count: int
    checkpoints: tuple[int, ...]
    probe_checkpoints: tuple[int, ...]
    test_count: int
    final_history: int

    def as_dict(self) -> dict:
        result = asdict(self)
        result["checkpoints"] = list(self.checkpoints)
        result["probe_checkpoints"] = list(self.probe_checkpoints)
        return result


FORMAL_SCHEDULE = RunSchedule(60, (20, 40, 60), (20, 40), 15, 60)
PILOT_SCHEDULE = RunSchedule(20, (10, 20), (10,), 5, 20)


def schedule_for_kind(kind: str) -> RunSchedule:
    if kind == "formal":
        return FORMAL_SCHEDULE
    if kind == "pilot_run":
        return PILOT_SCHEDULE
    raise ValueError(f"unknown r21 project kind: {kind}")


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


def expected_question_calls(memory_count: int, test_count: int, probes: int) -> int:
    """One runner x three trajectories x three conditions."""
    return 6 * memory_count + 9 * PROBE_TEST_COUNT * probes + 18 * test_count


class ProjectKind(StrEnum):
    pilot_run = "pilot_run"
    formal = "formal"


class RunnerConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: str = Field(min_length=1, max_length=24)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)


class PromptTemplates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scoring: str = Field(min_length=1, max_length=12000)
    crm_update: str = Field(min_length=1, max_length=12000)
    arm_update: str = Field(min_length=1, max_length=12000)


class ScoreOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=3.5)
    feedback: str = Field(min_length=1, max_length=1200)

    @field_validator("feedback")
    @classmethod
    def feedback_is_compact(cls, value: str) -> str:
        if token_count(value) > MAX_FEEDBACK_TOKENS:
            raise ValueError(f"feedback exceeds {MAX_FEEDBACK_TOKENS} tokens")
        return value


class CompactRuleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1, max_length=640)
    effect: Literal["supports", "weakens"]
    importance: Literal["major", "moderate", "minor"]
    guidance: str = Field(min_length=1, max_length=960)
    support: str = Field(min_length=1, max_length=640)


class RubricAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sufficient: str = Field(min_length=1, max_length=960)
    partial: str = Field(min_length=1, max_length=960)
    missing: str = Field(min_length=1, max_length=960)


class AbstractRubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=640)
    importance: Literal["major", "moderate", "minor"]
    anchors: RubricAnchors
    support: str = Field(min_length=1, max_length=640)


class CRMUpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[CompactRuleItem] = Field(max_length=MAX_ITEMS)


class ARMUpdateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric: list[AbstractRubricItem] = Field(max_length=MAX_ITEMS)


def output_schema(kind: str, condition: str | None):
    if kind == "test_score":
        return ScoreOutput
    if condition == "crm":
        return CRMUpdateOutput
    if condition == "arm":
        return ARMUpdateOutput
    raise ValueError(f"unknown r21 call {kind}/{condition}")


__all__ = [
    "FINAL_REPEATS",
    "FORMAL_SCHEDULE",
    "MAX_CORE_TOKENS",
    "MAX_FEEDBACK_TOKENS",
    "MAX_GUIDANCE_TOKENS",
    "MAX_STORED_TOKENS",
    "MAX_SUPPORT_TOKENS",
    "MAX_VISIBLE_TOKENS",
    "PILOT_SCHEDULE",
    "PROMPT_ENVELOPE_VERSION",
    "ProjectKind",
    "PromptTemplates",
    "RunnerConfigIn",
    "RunSchedule",
    "TRAJECTORIES",
    "expected_question_calls",
    "output_schema",
    "schedule_for_kind",
    "schedule_from_manifest",
]
