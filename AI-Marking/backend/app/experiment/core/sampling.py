"""Deterministic, rebuildable sampling for the unified experiment core.

Every selection decision is a pure function of ``(seed, item key)`` hashes plus
the template's quota plan, so a frozen run snapshot can always be rebuilt from
the dataset audit.  Stratified Hamilton allocation gives every selected item a
recordable first-order inclusion probability ``pi``; the design weight is
``1/pi``.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass


def stable_key(*parts: str) -> str:
    """Domain-separated SHA-256 used for every deterministic tie-break."""
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def stable_rank(*parts: str) -> str:
    """Alias of :func:`stable_key` used for ascending lexicographic ordering."""
    return stable_key(*parts)


def normalized_prompt(prompt: str) -> str:
    """Whitespace-collapsed prompt text used for stratification cells."""
    return " ".join(prompt.split())


def hamilton_allocate(sizes: Mapping[str, int], seats: int) -> dict[str, int]:
    """Apportion ``seats`` across units by largest remainder, capped at size.

    Deterministic: ties on fractional remainder are broken by unit key.  The
    result always sums to ``min(seats, total)`` and never exceeds a unit size.
    """
    sizes = {key: int(size) for key, size in sizes.items()}
    if any(size < 0 for size in sizes.values()):
        raise ValueError("cell sizes must be non-negative")
    total = sum(sizes.values())
    if seats < 0:
        raise ValueError("seats must be non-negative")
    if seats >= total:
        return dict(sizes)

    quotas = {key: seats * size / total for key, size in sizes.items()}
    allocation = {key: min(int(math.floor(quotas[key])), size) for key, size in sizes.items()}
    remaining = seats - sum(allocation.values())
    order = sorted(
        sizes,
        key=lambda key: (-(quotas[key] - math.floor(quotas[key])), key),
    )
    index = 0
    while remaining > 0:
        key = order[index % len(order)]
        if allocation[key] < sizes[key]:
            allocation[key] += 1
            remaining -= 1
        index += 1
    return allocation


@dataclass(frozen=True, slots=True)
class SampleItem:
    """One auditable dataset input offered to a template's sampling plan."""

    key: str  # input_sha256
    stratum: int
    cell: str  # normalized prompt text (or any template-defined cell key)
    forced: bool = False


@dataclass(frozen=True, slots=True)
class Selection:
    """A selected input with its design-weight record."""

    key: str
    stratum: int
    cell_key: str
    inclusion_probability: float
    design_weight: float
    forced: bool


def stratified_select(
    items: Sequence[SampleItem],
    *,
    quota_by_stratum: Mapping[int, int],
    seed: str,
) -> list[Selection]:
    """Select items by forced inclusion plus stratified Hamilton allocation.

    Forced items always enter (pi = 1) and consume their stratum quota first;
    the residual quota is apportioned across the stratum's cells proportional
    to cell size and filled within each cell by ascending ``stable_rank``.
    """
    duplicate = len({item.key for item in items}) != len(items)
    if duplicate:
        raise ValueError("sample items must have unique keys")
    unknown = sorted({item.stratum for item in items} - set(quota_by_stratum))
    if unknown:
        raise ValueError(f"sampling plan is missing strata quotas: {unknown}")

    by_stratum: dict[int, list[SampleItem]] = defaultdict(list)
    for item in items:
        by_stratum[item.stratum].append(item)

    selections: list[Selection] = []
    for stratum in sorted(quota_by_stratum):
        quota = int(quota_by_stratum[stratum])
        members = by_stratum.get(stratum, [])
        forced = [item for item in members if item.forced]
        if len(forced) > quota:
            raise ValueError(
                f"stratum {stratum} has {len(forced)} forced items but quota {quota}"
            )
        for item in forced:
            selections.append(
                Selection(
                    key=item.key,
                    stratum=stratum,
                    cell_key=_cell_key(item),
                    inclusion_probability=1.0,
                    design_weight=1.0,
                    forced=True,
                )
            )
        residual = quota - len(forced)
        pool = [item for item in members if not item.forced]
        if residual > len(pool):
            raise ValueError(
                f"stratum {stratum} quota {quota} exceeds its {len(pool)} remaining items"
            )
        if residual == 0:
            continue
        cells: dict[str, list[SampleItem]] = defaultdict(list)
        for item in pool:
            cells[_cell_key(item)].append(item)
        sizes = {cell: len(members) for cell, members in cells.items()}
        allocation = hamilton_allocate(sizes, residual)
        for cell in sorted(allocation):
            take = allocation[cell]
            if take == 0:
                continue
            members = cells[cell]
            ordered = sorted(
                members, key=lambda item: stable_rank(seed, "cell", cell, item.key)
            )
            chosen = ordered[:take]
            probability = take / len(members)
            for item in chosen:
                selections.append(
                    Selection(
                        key=item.key,
                        stratum=stratum,
                        cell_key=cell,
                        inclusion_probability=probability,
                        design_weight=1.0 / probability,
                        forced=False,
                    )
                )
    return selections


def select_retest(
    selected: Collection[Selection],
    *,
    quota_by_stratum: Mapping[int, int],
    seed: str,
) -> list[str]:
    """Pick the retest subset from already-selected items, stratified again."""
    by_stratum: dict[int, list[Selection]] = defaultdict(list)
    for item in selected:
        by_stratum[item.stratum].append(item)
    keys: list[str] = []
    for stratum in sorted(quota_by_stratum):
        quota = int(quota_by_stratum[stratum])
        members = by_stratum.get(stratum, [])
        if quota > len(members):
            raise ValueError(
                f"stratum {stratum} retest quota {quota} exceeds its {len(members)} selected items"
            )
        ordered = sorted(
            members, key=lambda item: stable_rank(seed, "retest", item.key)
        )
        keys.extend(item.key for item in ordered[:quota])
    return keys


def _cell_key(item: SampleItem) -> str:
    return stable_key("cell", item.cell)[:32]


__all__ = [
    "SampleItem",
    "Selection",
    "hamilton_allocate",
    "normalized_prompt",
    "select_retest",
    "stable_key",
    "stratified_select",
]
