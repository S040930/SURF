"""Deterministic r22 call sequencing and MAE-ready call metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.experiment.r22.protocol import (
    CONDITIONS,
    PILOT_SCHEDULE,
    TEST_REPEATS,
    TRAIN_REPEATS,
    RunSchedule,
)


@dataclass(frozen=True, slots=True)
class CallSpec:
    kind: str
    question_id: str
    condition: str
    trajectory: int
    answer_id: str
    history_count: int
    repeat: int
    position: int | None = None


def call_sequence(
    question_id: str,
    condition: str,
    trajectory: int,
    memory_answer_ids: Iterable[str],
    test_answer_ids: Iterable[str],
    schedule: RunSchedule = PILOT_SCHEDULE,
) -> list[CallSpec]:
    """Build one stream with pre-update training scores and checkpoint tests."""
    memory = list(memory_answer_ids)
    tests = list(test_answer_ids)
    if len(memory) != schedule.memory_count:
        raise ValueError(
            f"r22 requires {schedule.memory_count} training answers, got {len(memory)}"
        )
    if len(tests) != schedule.test_count:
        raise ValueError(
            f"r22 requires {schedule.test_count} test answers, got {len(tests)}"
        )
    if condition not in CONDITIONS:
        raise ValueError(f"unknown r22 condition: {condition}")

    specs: list[CallSpec] = []
    for position, answer_id in enumerate(memory, start=1):
        specs.extend(
            CallSpec(
                kind="train_score",
                question_id=question_id,
                condition=condition,
                trajectory=trajectory,
                answer_id=answer_id,
                history_count=position - 1,
                repeat=repeat,
                position=position,
            )
            for repeat in TRAIN_REPEATS
        )
        if condition != "nm":
            specs.append(
                CallSpec(
                    kind="memory_update",
                    question_id=question_id,
                    condition=condition,
                    trajectory=trajectory,
                    answer_id=answer_id,
                    history_count=position,
                    repeat=0,
                    position=position,
                )
            )
        if position in schedule.checkpoints:
            specs.extend(
                CallSpec(
                    kind="test_score",
                    question_id=question_id,
                    condition=condition,
                    trajectory=trajectory,
                    answer_id=test_id,
                    history_count=position,
                    repeat=repeat,
                )
                for test_id in tests
                for repeat in TEST_REPEATS
            )
    return specs


def expected_total_calls(
    question_count: int = 2, schedule: RunSchedule = PILOT_SCHEDULE
) -> int:
    """Expected primary calls across all questions and streams."""
    return question_count * (
        schedule.memory_count * len(("crm", "arm")) * 3
        + schedule.memory_count * len(CONDITIONS) * 3 * len(TRAIN_REPEATS)
        + schedule.test_count
        * len(schedule.checkpoints)
        * len(CONDITIONS)
        * 3
        * len(TEST_REPEATS)
    )


__all__ = ["CallSpec", "call_sequence", "expected_total_calls"]
