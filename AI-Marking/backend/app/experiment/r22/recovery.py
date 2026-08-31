"""Orchestration contract for one r22 primary/compression logical call."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from app.experiment.r22.compression import (
    CandidateCheck,
    CompressionResult,
    CompressionRunner,
    CompressionValidationError,
    compress_candidate,
    inspect_candidate,
    sha256_json,
    token_count_map,
)


class TerminalRecoveryError(RuntimeError):
    """A logical call cannot be safely recovered and must be terminal."""

    def __init__(
        self,
        message: str,
        *,
        check: CandidateCheck,
        candidate: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.check = check
        self.candidate = candidate


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    value: dict[str, Any]
    primary_original: dict[str, Any]
    primary_check: CandidateCheck
    compression: CompressionResult | None
    primary_latency_ms: int

    @property
    def compressed(self) -> bool:
        return self.compression is not None


def recover_candidate(
    runner: CompressionRunner,
    *,
    kind: str,
    condition: str | None,
    primary_value: dict[str, Any],
    runtime: dict[str, Any],
    primary_latency_ms: int = 0,
    answer: str = "",
    question: str = "question words",
    reference_answer: str = "reference words",
) -> RecoveryOutcome:
    """Accept a primary candidate or invoke exactly one compression call."""
    started = time.monotonic()
    check = inspect_candidate(
        kind,
        condition,
        primary_value,
        answer=answer,
        question=question,
        reference_answer=reference_answer,
    )
    if check.accepted:
        return RecoveryOutcome(
            value=check.value or {},
            primary_original=copy.deepcopy(primary_value),
            primary_check=check,
            compression=None,
            primary_latency_ms=primary_latency_ms or int((time.monotonic() - started) * 1000),
        )
    if not check.needs_compression:
        raise TerminalRecoveryError(check.error or "primary candidate failed validation", check=check)
    try:
        compressed = compress_candidate(
            runner,
            kind=kind,
            condition=condition,
            value=check.value or primary_value,
            runtime=runtime,
            answer=answer,
            question=question,
            reference_answer=reference_answer,
        )
    except CompressionValidationError as exc:
        raise TerminalRecoveryError(
            str(exc), check=check, candidate=exc.candidate
        ) from exc
    return RecoveryOutcome(
        value=compressed.compressed,
        primary_original=copy.deepcopy(primary_value),
        primary_check=check,
        compression=compressed,
        primary_latency_ms=primary_latency_ms or int((time.monotonic() - started) * 1000),
    )


def audit_payload(
    *,
    project_id: str,
    logical_call_key: str,
    kind: str,
    condition: str | None,
    outcome: RecoveryOutcome,
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    """Produce ORM-ready audit dictionaries for primary and optional compression."""
    primary = outcome.primary_check
    rows = [
        {
            "project_id": project_id,
            "logical_call_key": logical_call_key,
            "kind": kind,
            "condition": condition,
            "stage": "primary",
            "outcome": "needs_compression" if primary.needs_compression else "accepted",
            "original_json": outcome.primary_original,
            "output_json": primary.value,
            "original_sha256": sha256_json(outcome.primary_original),
            "output_sha256": sha256_json(primary.value or {}),
            "original_token_counts_json": token_count_map(kind, condition, outcome.primary_original),
            "output_token_counts_json": token_count_map(kind, condition, primary.value or {}),
            "violations_json": [item.as_dict() for item in primary.violations],
            "runtime_json": dict(runtime),
            "latency_ms": outcome.primary_latency_ms,
        }
    ]
    if outcome.compression:
        compressed = outcome.compression
        rows.append(
            {
                "project_id": project_id,
                "logical_call_key": logical_call_key,
                "kind": kind,
                "condition": condition,
                "stage": "compression",
                "outcome": "accepted",
                "original_json": compressed.original,
                "output_json": compressed.compressed,
                "original_sha256": compressed.original_sha256,
                "output_sha256": compressed.compressed_sha256,
                "original_token_counts_json": token_count_map(kind, condition, compressed.original),
                "output_token_counts_json": token_count_map(kind, condition, compressed.compressed),
                "violations_json": [item.as_dict() for item in compressed.violations],
                "runtime_json": dict(runtime),
                "latency_ms": compressed.latency_ms,
            }
        )
    return rows


def terminal_audit_payload(
    *,
    project_id: str,
    logical_call_key: str,
    kind: str,
    condition: str | None,
    outcome: str,
    runtime: dict[str, Any],
    error_message: str,
    original: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    stage: str = "primary",
) -> dict[str, Any]:
    """Build an audit row for a terminal non-recoverable primary failure."""
    return {
        "project_id": project_id,
        "logical_call_key": logical_call_key,
        "kind": kind,
        "condition": condition,
        "stage": stage,
        "outcome": outcome,
        "original_json": original,
        "output_json": output,
        "violations_json": [],
        "runtime_json": dict(runtime),
        "latency_ms": 0,
        "error_message": error_message[:4000],
    }


__all__ = [
    "RecoveryOutcome",
    "TerminalRecoveryError",
    "audit_payload",
    "recover_candidate",
    "terminal_audit_payload",
]
