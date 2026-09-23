"""Frozen protocol constants and wire schemas for the SAF memory study.

The original V3 protocol remains readable for historical projects. V3-r2 is
the only protocol used for new projects and keeps the V3 experimental queue
while replacing the scoring instrument and formal question set.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.experiment.memory_study import (
    DEFAULT_PROTOCOL_ID,
    PROTOCOL_ID,
    SUPPORTED_PROTOCOL_IDS,
    V3_R2_PROTOCOL_ID,
)

ARCHIVE_SHA256 = "c0841b36acdbe4adff9d96ede366fbe52de5671d859642783266204b4c9f5ec0"
SAF_COMMIT = "09949e912d266777a752ac7d51cfcddb4b3f6978"
MEM0_COMMIT = "c7ee362aff94a369af70f13f2b4f853f6793ff4c"
AMEM_COMMIT = "f303dfc71e07bdc787f4bc135d4cea328ae30e99"
SELECTION_SEED = "saf-memory-framework-v1"
EMBEDDING_MODEL = "doubao-embedding-vision"
EMBEDDING_REVISION_ENV = "MEMORY_STUDY_EMBEDDING_REVISION"
TOKENIZER_NAME = "o200k_base"
TOKENIZER_VERSION = "0.13.0"

FORMAL_QUESTION_COUNT = 6
FORMAL_TRAIN_COUNT = 40
FORMAL_TEST_COUNT = 10

# V3-r2 freezes the six-question selection result independently of the
# historical protocol that originally produced that selection.
V3_R2_FORMAL_QUESTIONS = (
    "12.2_PE",
    "10.2_TC",
    "8.2_MM",
    "4.13",
    "8.1_MM",
    "4.3",
)

PILOT_TRAIN_COUNT = 20
PILOT_TEST_COUNT = 5
PILOT_QUESTION_COUNT = 2
DEVELOPMENT_QUESTION_COUNT = 2
# The historical V3 protocol used three hash-derived order variants.  Keep
# those names available for old projects, but V3-r2 follows the V4 ordering
# contract: the archive order and one deterministic shuffle.
LEGACY_ORDER_VARIANTS = ("order_1", "order_2", "order_3")
ORDER_VARIANTS = ("original", "shuffled")
ORDER_SHUFFLE_SEED = 42

# V3 deliberately runs one model so the formal study remains a cost-sensitive
# Luna-only experiment. Keep this tuple because queue/accounting code iterates
# over the frozen model list.
MODEL_IDS = ("gpt-6-luna",)
CONDITIONS = (
    "no_memory",
    "retrieval_full",
    "mem0_full",
    "amem_full",
    "retrieval_no_feedback",
    "mem0_no_feedback",
    "amem_no_feedback",
)
FRAMEWORK_CONDITIONS = tuple(value for value in CONDITIONS if value != "no_memory")
MEMORY_WRITE_CONDITIONS = (
    "mem0_full",
    "amem_full",
    "mem0_no_feedback",
    "amem_no_feedback",
)
DETERMINISTIC_MEMORY_CONDITIONS = ("retrieval_full", "retrieval_no_feedback")
FEEDBACK_MODES = ("full", "no_feedback")
# V3-r2 uses a smaller retrieval dose after the formal-1 diagnostic. Legacy
# V3 projects retain their original top-20 setting through the accessor below.
LEGACY_RETRIEVAL_TOP_K = 20
RETRIEVAL_TOP_K = 5
MEMORY_PROFILE = "legacy_v1"
INITIAL_TIMEOUT_SECONDS = 120
REASONING_EFFORT = "medium"
SPEED_MODE = "standard"
MAX_CODEX_SUBPROCESSES = 4
FINAL_REPEATS = (1,)

# Formal V3 launches the four base conditions in one wave. The three
# feedback-free memory variants remain available as an explicit second wave;
# development/pilot rows retain the historical expanded condition list.
BASE_CONDITIONS = ("no_memory", "retrieval_full", "mem0_full", "amem_full")
OPTIONAL_NO_FEEDBACK_CONDITIONS = (
    "retrieval_no_feedback",
    "mem0_no_feedback",
    "amem_no_feedback",
)

PROMPT_ENVELOPE_VERSION = "saf-memory-study-codex-exec-v2"
LEGACY_SCORING_SYSTEM_PROMPT = (
    "Score the student answer against the reference answer. Return only JSON "
    "with numeric score and concise feedback. Never use verification feedback "
    "or any hidden evaluation label."
)
LEGACY_SCORING_PAYLOAD_FIELDS = (
    "answer",
    "condition",
    "max_score",
    "memory",
    "question",
    "reference_answer",
)
V3_R2_SCORING_SYSTEM_PROMPT = (
    "Score the student answer against the reference answer. Use the reference "
    "answer as the content standard. Award partial credit for correct, relevant, "
    "and complete content; accept valid paraphrases and equivalent reasoning. "
    "Use historical cases only to calibrate grading style and judge the current "
    "answer independently. Return only JSON with numeric score and concise "
    "feedback. The score must be within the supplied score_floor and "
    "score_ceiling. Never use verification feedback, condition labels, retrieval "
    "metadata, or hidden evaluation labels."
)
V3_R2_SCORING_PAYLOAD_FIELDS = (
    "answer",
    "memory",
    "question",
    "reference_answer",
    "score_ceiling",
    "score_floor",
)


def scoring_context_for_protocol(protocol_id: str) -> dict[str, object]:
    """Return the immutable scoring-instrument declaration for a protocol."""
    if protocol_id == PROTOCOL_ID:
        context: dict[str, object] = {
            "protocol_id": protocol_id,
            "envelope_version": PROMPT_ENVELOPE_VERSION,
            "system_prompt": LEGACY_SCORING_SYSTEM_PROMPT,
            "payload_fields": list(LEGACY_SCORING_PAYLOAD_FIELDS),
            "memory_projection": "legacy_raw_retrieval",
        }
    elif protocol_id == V3_R2_PROTOCOL_ID:
        context = {
            "protocol_id": protocol_id,
            "envelope_version": PROMPT_ENVELOPE_VERSION,
            "system_prompt": V3_R2_SCORING_SYSTEM_PROMPT,
            "payload_fields": list(V3_R2_SCORING_PAYLOAD_FIELDS),
            "memory_projection": "student_answer_teacher_score_optional_feedback",
        }
    else:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    context["instrument_sha256"] = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return context


def is_legacy_protocol(protocol_id: str | None) -> bool:
    return protocol_id == PROTOCOL_ID


def is_v3_r2_protocol(protocol_id: str | None) -> bool:
    return protocol_id == V3_R2_PROTOCOL_ID


def order_variants_for_kind(
    kind: str, protocol_id: str = DEFAULT_PROTOCOL_ID
) -> tuple[str, ...]:
    """Return the immutable history-order plan for a study kind."""
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    if kind == StudyKind.formal:
        return LEGACY_ORDER_VARIANTS if protocol_id == PROTOCOL_ID else ORDER_VARIANTS
    if kind in {StudyKind.development, StudyKind.pilot}:
        return (
            ("order_1",)
            if protocol_id == PROTOCOL_ID
            else ("original",)
        )
    raise ValueError(f"unknown study kind: {kind}")


def order_variants_for_condition(
    kind: str,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
    condition: str | None = None,
) -> tuple[str, ...]:
    """Return the protocol-specific history-order plan for one condition."""
    del condition
    return order_variants_for_kind(kind, protocol_id)


def repeats_for_kind(
    kind: str, protocol_id: str = DEFAULT_PROTOCOL_ID
) -> tuple[int, ...]:
    """Return the immutable scoring-repeat plan."""
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    if kind == StudyKind.formal:
        return FINAL_REPEATS
    if kind in {StudyKind.development, StudyKind.pilot}:
        return (1,)
    raise ValueError(f"unknown study kind: {kind}")


def conditions_for_kind(
    kind: str, protocol_id: str = DEFAULT_PROTOCOL_ID
) -> tuple[str, ...]:
    """Return the immutable V3 condition wave for a new project."""
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    if kind == StudyKind.formal:
        return BASE_CONDITIONS
    return CONDITIONS


def framework_conditions_for_protocol(protocol_id: str) -> tuple[str, ...]:
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    return FRAMEWORK_CONDITIONS


def memory_write_conditions_for_protocol(protocol_id: str) -> tuple[str, ...]:
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    return MEMORY_WRITE_CONDITIONS


def deterministic_memory_conditions_for_protocol(protocol_id: str) -> tuple[str, ...]:
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    return DETERMINISTIC_MEMORY_CONDITIONS


def retrieval_top_k_for_protocol(protocol_id: str) -> int:
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    return (
        LEGACY_RETRIEVAL_TOP_K
        if protocol_id == PROTOCOL_ID
        else RETRIEVAL_TOP_K
    )


def memory_profile_for_protocol(protocol_id: str) -> str:
    if protocol_id not in SUPPORTED_PROTOCOL_IDS:
        raise ValueError(f"unknown memory-study protocol: {protocol_id}")
    return MEMORY_PROFILE


class StudyKind(StrEnum):
    development = "development"
    pilot = "pilot"
    formal = "formal"


class StudyStatus(StrEnum):
    draft = "draft"
    ready = "ready"
    frozen = "frozen"
    running = "running"
    paused = "paused"
    attention_required = "attention_required"
    completed = "completed"
    terminated = "terminated"


class CallKind(StrEnum):
    memory_write = "memory_write"
    score = "score"


class CallStatus(StrEnum):
    pending = "pending"
    leased = "leased"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ScoreOutput(BaseModel):
    """Continuous score response with concise auxiliary feedback."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0)
    feedback: str = Field(min_length=1, max_length=100_000)


class PreflightOutput(BaseModel):
    """Small structured response used to validate a frozen Codex runtime."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    probe_token: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("probe_token", "probe"),
        serialization_alias="probe_token",
    )


class StudyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["development", "pilot", "formal"]
    protocol_id: Literal[
        "saf-memory-framework-v3", "saf-memory-framework-v3-r2"
    ] = DEFAULT_PROTOCOL_ID
    data_processing_confirmed: Literal[True]
    speed_modes: dict[str, Literal["standard", "fast"]] = Field(
        default_factory=dict,
        description="Per-model runner binding: model -> speed_mode; "
        "unknown models are rejected by the service layer.",
    )
    feedback_waves: list[Literal["full", "no_feedback"]] = Field(
        default_factory=lambda: ["full"],
        min_length=1,
        max_length=2,
        description="Formal studies may explicitly add the independent no-feedback wave.",
    )


def counts_for_kind(kind: str) -> dict[str, int]:
    """Return fixed sample sizes; no API payload can override them."""
    if kind == StudyKind.formal:
        return {
            "questions": FORMAL_QUESTION_COUNT,
            "training_per_question": FORMAL_TRAIN_COUNT,
            "test_per_question": FORMAL_TEST_COUNT,
        }
    return {
        "questions": PILOT_QUESTION_COUNT,
        "training_per_question": PILOT_TRAIN_COUNT,
        "test_per_question": PILOT_TEST_COUNT,
    }


def expected_score_calls(
    kind: str,
    feedback_waves: tuple[str, ...] | list[str] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> int:
    counts = counts_for_kind(kind)
    conditions = conditions_for_kind(kind, protocol_id)
    if kind == StudyKind.formal and feedback_waves and "no_feedback" in feedback_waves:
        conditions = conditions + OPTIONAL_NO_FEEDBACK_CONDITIONS
    train_scores = counts["training_per_question"] if kind == StudyKind.formal else 0
    return (
        counts["questions"]
        * len(MODEL_IDS)
        * len(order_variants_for_kind(kind, protocol_id))
        * len(conditions)
        * (counts["test_per_question"] + train_scores)
        * len(repeats_for_kind(kind, protocol_id))
    )


def expected_memory_write_calls(
    kind: str,
    feedback_waves: tuple[str, ...] | list[str] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> int:
    counts = counts_for_kind(kind)
    conditions = (
        ("retrieval_full", "mem0_full", "amem_full")
        if kind == StudyKind.formal
        else MEMORY_WRITE_CONDITIONS
    )
    if kind == StudyKind.formal and feedback_waves and "no_feedback" in feedback_waves:
        conditions = conditions + OPTIONAL_NO_FEEDBACK_CONDITIONS
    return (
        counts["questions"]
        * len(MODEL_IDS)
        * len(order_variants_for_kind(kind, protocol_id))
        * len(conditions)
        * counts["training_per_question"]
    )


def expected_call_counts(
    kind: str,
    feedback_waves: tuple[str, ...] | list[str] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> dict[str, int]:
    counts = counts_for_kind(kind)
    conditions = conditions_for_kind(kind, protocol_id)
    if kind == StudyKind.formal and feedback_waves and "no_feedback" in feedback_waves:
        conditions = conditions + OPTIONAL_NO_FEEDBACK_CONDITIONS
    write_conditions = (
        ("retrieval_full", "mem0_full", "amem_full")
        if kind == StudyKind.formal
        else MEMORY_WRITE_CONDITIONS
    )
    if kind == StudyKind.formal and feedback_waves and "no_feedback" in feedback_waves:
        write_conditions = write_conditions + OPTIONAL_NO_FEEDBACK_CONDITIONS
    expanded_memory = (
        counts["questions"]
        * len(MODEL_IDS)
        * len(order_variants_for_kind(kind, protocol_id))
        * len(write_conditions)
        * counts["training_per_question"]
    )
    deterministic_materializations = (
        counts["questions"]
        * len(MODEL_IDS)
        * len(order_variants_for_kind(kind, protocol_id))
        * len([value for value in conditions if value.startswith("retrieval")])
    )
    return {
        "score_calls": expected_score_calls(kind, feedback_waves, protocol_id),
        "memory_write_calls": expected_memory_write_calls(
            kind, feedback_waves, protocol_id
        ),
        "expanded_memory_store_writes": expanded_memory,
        "deterministic_retrieval_materializations": deterministic_materializations,
        "order_variant_count": len(order_variants_for_kind(kind, protocol_id)),
        "repeat_count": len(repeats_for_kind(kind, protocol_id)),
        "total_score_results": expected_score_calls(
            kind, feedback_waves, protocol_id
        ),
    }


__all__ = [
    "ARCHIVE_SHA256",
    "AMEM_COMMIT",
    "BASE_CONDITIONS",
    "CallKind",
    "CallStatus",
    "CONDITIONS",
    "DEFAULT_PROTOCOL_ID",
    "DETERMINISTIC_MEMORY_CONDITIONS",
    "DEVELOPMENT_QUESTION_COUNT",
    "EMBEDDING_MODEL",
    "EMBEDDING_REVISION_ENV",
    "FINAL_REPEATS",
    "FEEDBACK_MODES",
    "FORMAL_QUESTION_COUNT",
    "FORMAL_TEST_COUNT",
    "FORMAL_TRAIN_COUNT",
    "FRAMEWORK_CONDITIONS",
    "INITIAL_TIMEOUT_SECONDS",
    "MAX_CODEX_SUBPROCESSES",
    "MEM0_COMMIT",
    "MEMORY_PROFILE",
    "MEMORY_WRITE_CONDITIONS",
    "MODEL_IDS",
    "OPTIONAL_NO_FEEDBACK_CONDITIONS",
    "LEGACY_ORDER_VARIANTS",
    "ORDER_VARIANTS",
    "ORDER_SHUFFLE_SEED",
    "PROMPT_ENVELOPE_VERSION",
    "PILOT_QUESTION_COUNT",
    "PILOT_TEST_COUNT",
    "PILOT_TRAIN_COUNT",
    "PreflightOutput",
    "PROTOCOL_ID",
    "REASONING_EFFORT",
    "RETRIEVAL_TOP_K",
    "LEGACY_RETRIEVAL_TOP_K",
    "SAF_COMMIT",
    "SELECTION_SEED",
    "SPEED_MODE",
    "ScoreOutput",
    "StudyCreate",
    "StudyKind",
    "StudyStatus",
    "SUPPORTED_PROTOCOL_IDS",
    "TOKENIZER_NAME",
    "TOKENIZER_VERSION",
    "V3_R2_FORMAL_QUESTIONS",
    "V3_R2_PROTOCOL_ID",
    "V3_R2_SCORING_PAYLOAD_FIELDS",
    "V3_R2_SCORING_SYSTEM_PROMPT",
    "conditions_for_kind",
    "counts_for_kind",
    "deterministic_memory_conditions_for_protocol",
    "expected_call_counts",
    "expected_memory_write_calls",
    "expected_score_calls",
    "framework_conditions_for_protocol",
    "is_legacy_protocol",
    "is_v3_r2_protocol",
    "memory_profile_for_protocol",
    "memory_write_conditions_for_protocol",
    "order_variants_for_condition",
    "order_variants_for_kind",
    "retrieval_top_k_for_protocol",
    "repeats_for_kind",
    "scoring_context_for_protocol",
]
