"""Primary-call entry point that wires the r22 runner to recovery."""

from __future__ import annotations

from typing import Any

from app.experiment.r21.codex_runner import CodexResult
from app.experiment.r22.protocol import transport_schema
from app.experiment.r22.recovery import RecoveryOutcome, recover_candidate


def run_with_recovery(
    runner,
    *,
    messages: tuple[dict[str, str], ...],
    kind: str,
    condition: str | None,
    runtime: dict[str, Any],
    answer: str = "",
    question: str = "question words",
    reference_answer: str = "reference words",
) -> RecoveryOutcome:
    """Run one primary call and invoke at most one compression call."""
    result: CodexResult = runner.run(
        messages=messages,
        # The r22 runner captures a complete JSON object first.  The explicit
        # schema documents the intended transport shape for compatible fakes.
        schema=transport_schema(kind, condition),
        runtime=runtime,
    )
    return recover_candidate(
        runner,
        kind=kind,
        condition=condition,
        primary_value=result.value,
        runtime=runtime,
        primary_latency_ms=result.latency_ms,
        answer=answer,
        question=question,
        reference_answer=reference_answer,
    )


__all__ = ["run_with_recovery"]
