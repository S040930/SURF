"""Descriptive recovery metrics for r22 reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable


def summarize_recovery(audits: Iterable[object]) -> dict:
    """Aggregate audit rows without treating compression as a model improvement."""
    rows = list(audits)
    grouped: dict[tuple[str, str, str], list[object]] = defaultdict(list)
    for row in rows:
        grouped[(row.kind, row.condition or "nm", row.outcome)].append(row)
    outcome_counts = Counter(row.outcome for row in rows)
    compression_rows = [row for row in rows if row.stage == "compression"]
    return {
        "audit_count": len(rows),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "primary_count": sum(row.stage == "primary" for row in rows),
        "compression_count": len(compression_rows),
        "compression_success_count": sum(row.outcome == "accepted" for row in compression_rows),
        "extra_calls": len(compression_rows),
        "compression_latency_ms": [row.latency_ms for row in compression_rows],
        "by_kind_condition_outcome": {
            f"{kind}/{condition}/{outcome}": len(items)
            for (kind, condition, outcome), items in sorted(grouped.items())
        },
        "interpretation": "descriptive processing-integrity metrics only",
    }


__all__ = ["summarize_recovery"]
