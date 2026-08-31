"""Single-shot, auditable recovery for complete token-overlong JSON outputs."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.experiment.r20.tokenization import token_count
from app.experiment.r22 import COMPRESSION_VERSION
from app.experiment.r22.memory import parse_memory_update, validate_memory_semantics
from app.experiment.r22.protocol import (
    MAX_CORE_TOKENS,
    MAX_FEEDBACK_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
    output_schema,
    transport_schema,
)


class CompressionRunner(Protocol):
    def run(self, *, messages, schema, runtime): ...


@dataclass(frozen=True, slots=True)
class TokenViolation:
    field: str
    actual_tokens: int
    maximum_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "actual_tokens": self.actual_tokens,
            "maximum_tokens": self.maximum_tokens,
        }


@dataclass(frozen=True, slots=True)
class CandidateCheck:
    status: str
    value: dict[str, Any] | None
    violations: tuple[TokenViolation, ...] = ()
    error: str | None = None

    @property
    def needs_compression(self) -> bool:
        return self.status == "needs_compression"

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True, slots=True)
class CompressionResult:
    original: dict[str, Any]
    compressed: dict[str, Any]
    violations: tuple[TokenViolation, ...]
    original_sha256: str
    compressed_sha256: str
    latency_ms: int


class CompressionValidationError(ValueError):
    """The compressor changed protected data or did not produce a valid result."""

    def __init__(self, message: str, *, candidate: dict[str, Any] | None = None):
        super().__init__(message)
        self.candidate = candidate


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def token_count_map(kind: str, condition: str | None, value: dict[str, Any]) -> dict[str, int]:
    """Return auditable token totals for the constrained fields."""
    if kind == "test_score":
        return {"feedback": token_count(str(value.get("feedback", "")))}
    if kind != "memory_update" or condition not in {"crm", "arm"}:
        return {}
    items = value.get("rules" if condition == "crm" else "rubric", [])
    visible = 0
    fields: dict[str, int] = {}
    for index, item in enumerate(items):
        prefix = f"{condition}[{index}]"
        if condition == "crm":
            fields[f"{prefix}.condition"] = token_count(str(item.get("condition", "")))
            fields[f"{prefix}.guidance"] = token_count(str(item.get("guidance", "")))
            fields[f"{prefix}.support"] = token_count(str(item.get("support", "")))
            visible += sum(
                token_count(str(item.get(key, "")))
                for key in ("condition", "effect", "importance", "guidance")
            )
        else:
            fields[f"{prefix}.criterion"] = token_count(str(item.get("criterion", "")))
            anchors = item.get("anchors", {})
            fields[f"{prefix}.anchors"] = sum(
                token_count(str(anchors.get(key, "")))
                for key in ("sufficient", "partial", "missing")
            )
            fields[f"{prefix}.support"] = token_count(str(item.get("support", "")))
            visible += token_count(str(item.get("criterion", ""))) + token_count(
                str(item.get("importance", ""))
            ) + fields[f"{prefix}.anchors"]
    fields["memory.visible"] = visible
    fields["memory.stored"] = _string_tokens(items)
    return fields


def _string_tokens(value: Any) -> int:
    if isinstance(value, str):
        return token_count(value)
    if isinstance(value, dict):
        return sum(_string_tokens(item) for item in value.values())
    if isinstance(value, list):
        return sum(_string_tokens(item) for item in value)
    return 0


def token_violations(kind: str, condition: str | None, value: dict[str, Any]) -> tuple[TokenViolation, ...]:
    """Calculate every applicable token violation without raising on semantics."""
    violations: list[TokenViolation] = []
    if kind == "test_score":
        actual = token_count(str(value.get("feedback", "")))
        if actual > MAX_FEEDBACK_TOKENS:
            violations.append(TokenViolation("feedback", actual, MAX_FEEDBACK_TOKENS))
        return tuple(violations)

    if kind != "memory_update" or condition not in {"crm", "arm"}:
        return tuple(violations)
    items = value.get("rules" if condition == "crm" else "rubric", [])
    visible = 0
    stored = _string_tokens(items)
    for index, item in enumerate(items):
        prefix = f"{condition}[{index}]"
        if condition == "crm":
            core = token_count(str(item.get("condition", "")))
            detail = token_count(str(item.get("effect", ""))) + token_count(
                str(item.get("guidance", ""))
            )
            visible += core + token_count(str(item.get("effect", ""))) + token_count(
                str(item.get("importance", ""))
            ) + token_count(str(item.get("guidance", "")))
            if core > MAX_CORE_TOKENS:
                violations.append(TokenViolation(f"{prefix}.condition", core, MAX_CORE_TOKENS))
            if detail > MAX_GUIDANCE_TOKENS:
                violations.append(TokenViolation(f"{prefix}.effect+guidance", detail, MAX_GUIDANCE_TOKENS))
            support = token_count(str(item.get("support", "")))
            if support > MAX_SUPPORT_TOKENS:
                violations.append(TokenViolation(f"{prefix}.support", support, MAX_SUPPORT_TOKENS))
        else:
            core = token_count(str(item.get("criterion", "")))
            anchors = item.get("anchors", {})
            anchor_tokens = sum(token_count(str(anchors.get(key, ""))) for key in ("sufficient", "partial", "missing"))
            visible += core + token_count(str(item.get("importance", ""))) + anchor_tokens
            if core > MAX_CORE_TOKENS:
                violations.append(TokenViolation(f"{prefix}.criterion", core, MAX_CORE_TOKENS))
            if anchor_tokens > MAX_GUIDANCE_TOKENS:
                violations.append(TokenViolation(f"{prefix}.anchors", anchor_tokens, MAX_GUIDANCE_TOKENS))
            support = token_count(str(item.get("support", "")))
            if support > MAX_SUPPORT_TOKENS:
                violations.append(TokenViolation(f"{prefix}.support", support, MAX_SUPPORT_TOKENS))
    if visible > MAX_VISIBLE_TOKENS:
        violations.append(TokenViolation("memory.visible", visible, MAX_VISIBLE_TOKENS))
    if stored > MAX_STORED_TOKENS:
        violations.append(TokenViolation("memory.stored", stored, MAX_STORED_TOKENS))
    return tuple(violations)


def _is_token_error(message: str) -> bool:
    lowered = message.casefold()
    return "token" in lowered and any(
        marker in lowered for marker in ("exceed", "limit", "use")
    )


def inspect_candidate(
    kind: str,
    condition: str | None,
    value: dict[str, Any],
    *,
    answer: str = "",
    question: str = "question words",
    reference_answer: str = "reference words",
) -> CandidateCheck:
    """Run transport then final validation and classify only token failures."""
    try:
        transport_value = transport_schema(kind, condition).model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        return CandidateCheck("rejected", None, error=f"transport validation failed: {exc}")

    violations = token_violations(kind, condition, transport_value)
    try:
        if kind == "memory_update":
            validate_memory_semantics(
                condition or "", transport_value, answer, question, reference_answer
            )
            parse_memory_update(condition or "", transport_value, answer, question, reference_answer)
        else:
            output_schema(kind, condition).model_validate(transport_value)
    except (ValidationError, ValueError) as exc:
        if violations and _is_token_error(str(exc)):
            return CandidateCheck("needs_compression", transport_value, violations, str(exc))
        return CandidateCheck("rejected", transport_value, violations, str(exc))
    return CandidateCheck("accepted", transport_value)


def protected_projection(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    """Return the fields that a compressor is forbidden to change."""
    if kind == "test_score":
        return {"score": value["score"]}
    key = "rules" if kind == "memory_update" and "rules" in value else "rubric"
    protected = []
    for item in value[key]:
        fields = {"importance": item["importance"]}
        if key == "rules":
            fields["effect"] = item["effect"]
        protected.append(fields)
    return {"key": key, "item_count": len(value[key]), "items": protected}


def compression_messages(
    kind: str, condition: str | None, value: dict[str, Any], violations: tuple[TokenViolation, ...]
) -> tuple[dict[str, str], ...]:
    protected = "score" if kind == "test_score" else "item count, order, and enum fields"
    system = (
        f"You are the r22 compression worker, protocol {COMPRESSION_VERSION}. "
        "Return JSON only. Compress the supplied complete JSON candidate so it satisfies "
        "the listed token limits. Preserve all facts and meaning; do not add facts, "
        f"delete entries, reorder entries, or change protected fields ({protected}). "
        "Rewrite only text fields that are necessary. Do not include explanations."
    )
    payload = {
        "kind": kind,
        "condition": condition,
        "candidate": value,
        "token_violations": [item.as_dict() for item in violations],
        "instruction": "Return the same JSON shape and satisfy every listed maximum.",
    }
    return (
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    )


def compress_candidate(
    runner: CompressionRunner,
    *,
    kind: str,
    condition: str | None,
    value: dict[str, Any],
    runtime: dict[str, Any],
    answer: str = "",
    question: str = "question words",
    reference_answer: str = "reference words",
) -> CompressionResult:
    """Compress one candidate at most once, then require full revalidation."""
    original = copy.deepcopy(value)
    check = inspect_candidate(
        kind, condition, original, answer=answer, question=question, reference_answer=reference_answer
    )
    if not check.needs_compression:
        raise CompressionValidationError(check.error or "candidate does not need compression")
    started = time.monotonic()
    result = runner.run(
        messages=compression_messages(kind, condition, original, check.violations),
        schema=transport_schema(kind, condition),
        runtime=runtime,
    )
    compressed = result.value
    if protected_projection(kind, original) != protected_projection(kind, compressed):
        raise CompressionValidationError(
            "compressor changed protected fields", candidate=compressed
        )
    final = inspect_candidate(
        kind, condition, compressed, answer=answer, question=question, reference_answer=reference_answer
    )
    if not final.accepted:
        raise CompressionValidationError(
            final.error or "compressed candidate failed validation",
            candidate=compressed,
        )
    return CompressionResult(
        original=original,
        compressed=compressed,
        violations=check.violations,
        original_sha256=sha256_json(original),
        compressed_sha256=sha256_json(compressed),
        latency_ms=int((time.monotonic() - started) * 1000),
    )


__all__ = [
    "CandidateCheck",
    "CompressionResult",
    "CompressionValidationError",
    "TokenViolation",
    "compression_messages",
    "compress_candidate",
    "inspect_candidate",
    "protected_projection",
    "sha256_json",
    "token_count_map",
    "token_violations",
]
