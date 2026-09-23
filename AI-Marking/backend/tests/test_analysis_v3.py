"""Unit tests for the analysis_v3 addendum helpers (synthetic data only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_analysis_v3 import (  # noqa: E402
    _boot_ci,
    _cluster_boot_ci,
    _qwk,
    grid_forensics,
    stratum_of_item,
    zero_allocation_cells,
)
from build_closeout import holm_adjust, ss_decomposition  # noqa: E402

from app.experiment.core.contracts import Channel, ScoringContract  # noqa: E402
from app.experiment.core.registry import AuditItem  # noqa: E402
from app.experiment.core.sampling import (  # noqa: E402
    SampleItem,
    stratified_select,
)

CHANNELS = ("content", "organization", "language")


def _row(
    model: str, key: str, run: str, preds: dict[str, int], labels: dict[str, int]
) -> dict[str, str]:
    row = {c: "" for c in CHANNELS}
    row.update(
        {
            "model": model,
            "input_sha256": key,
            "run_index": run,
            **{f"prediction_{c}": str(preds[c]) for c in CHANNELS},
            **{f"label_{c}": str(labels[c]) for c in CHANNELS},
        }
    )
    return row


def test_grid_forensics_flags_half_integer_lattice() -> None:
    labels = {"content": 6, "organization": 7, "language": 5}
    luna_rows = [
        _row(
            "gpt-5.6-luna",
            f"key{i}",
            "0",
            {"content": 1 if i == 0 else 5, "organization": 7, "language": 3},
            labels,
        )
        for i in range(4)
    ]
    terra_rows = [
        _row(
            "gpt-5.6-terra",
            f"key{i}",
            "0",
            {"content": 4, "organization": 6, "language": 2},
            labels,
        )
        for i in range(4)
    ]
    report = grid_forensics(luna_rows + terra_rows)
    luna = report["per_model"]["gpt-5.6-luna"]["content"]
    assert luna["odd_x2_share"] == 1.0
    assert luna["distinct_x2_values"] == [1, 5]
    assert luna["predictions_at_grid_floor_x2"] == 1
    assert luna["grid_floor_rows"][0]["input_sha256"] == "key0"
    terra = report["per_model"]["gpt-5.6-terra"]["content"]
    assert terra["odd_x2_share"] == 0.0
    assert terra["predictions_at_grid_floor_x2"] == 0


def test_zero_allocation_cells_counts_uncovered_pool() -> None:
    def item(key: str, prompt: str, total_x2: int, forced: bool) -> AuditItem:
        labels = {
            "content": total_x2 // 3,
            "organization": total_x2 // 3,
            "language": total_x2 - 2 * (total_x2 // 3),
        }
        return AuditItem(
            key=key,
            prompt_sha256=key,
            prompt_norm=prompt,
            labels_x2=labels,
            word_count=100,
            provenance={"source_id": key},
        )

    # cell p2 only: forced item (excluded from the pool), so p2 has no
    # non-forced members; cells p3 (2 essays) and p4 (1 essay) form the pool.
    items = {
        "forced": item("forced", "p2", 6, True),
        "a1": item("a1", "p3", 20, False),
        "a2": item("a2", "p3", 22, False),
        "b1": item("b1", "p4", 20, False),
    }
    sample = [
        SampleItem(
            key=key, stratum=stratum_of_item(it), cell=it.prompt_norm, forced=False
        )
        for key, it in items.items()
        if key != "forced"
    ]
    stratum = stratum_of_item(items["a1"])
    plan = stratified_select(sample, quota_by_stratum={stratum: 1}, seed="t")
    zero = zero_allocation_cells(items, pilot_keys=set(), selections=plan)
    # one seat across p3(2)/p4(1) leaves exactly one cell uncovered
    total_uncovered = sum(sum(cells.values()) for cells in zero.values())
    assert total_uncovered == 1
    covered_cells = {sel.cell_key for sel in plan}
    for stratum_cells in zero.values():
        for cell in stratum_cells:
            assert cell not in covered_cells


def test_stratified_bootstrap_is_deterministic_and_centers_on_estimate() -> None:
    labels = [4, 5, 6, 6, 7, 8, 8, 9] * 3
    preds = [5, 5, 6, 7, 7, 8, 9, 9] * 3
    strata = [1] * 12 + [2] * 12
    first = _boot_ci(_qwk, labels, preds, strata=strata, seed="s1")
    second = _boot_ci(_qwk, labels, preds, strata=strata, seed="s1")
    assert first == second
    assert first["estimate"] == _qwk(labels, preds)
    assert first["ci_low"] <= first["estimate"] <= first["ci_high"]
    other = _boot_ci(_qwk, labels, preds, strata=strata, seed="s2")
    assert (other["ci_low"], other["ci_high"]) != (first["ci_low"], first["ci_high"])


def test_cluster_bootstrap_interval_contains_estimate() -> None:
    labels = [4, 5, 6, 7, 8, 9] * 4
    preds = [5, 5, 7, 7, 8, 8] * 4
    clusters = ["p1", "p2", "p3", "p4"] * 6
    result = _cluster_boot_ci(_qwk, labels, preds, clusters=clusters, seed="c1")
    assert result["clusters"] == 4
    assert result["ci_low"] <= result["estimate"] <= result["ci_high"]


def test_ss_decomposition_matches_hand_computation() -> None:
    bias = np.array([0.5, -0.5, 1.0, 1.5])
    clusters = np.array(["pA", "pA", "pB", "pB"])
    result = ss_decomposition(bias, clusters)
    # overall mean = 0.625; SS_between = 2*(0-0.625)^2 + 2*(1.25-0.625)^2 = 1.5625
    # SS_within = (0.5^2+0.5^2) + (0.25^2+0.25^2) = 0.625; share = 1.5625/2.1875
    assert result["ss_between_score_points2"] == 1.5625
    assert result["ss_within_score_points2"] == 0.625
    assert result["between_ss_share"] == 0.7143
    assert result["prompt_count"] == 2


def test_holm_adjustment_orders_and_caps() -> None:
    adjusted = holm_adjust({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5})
    assert adjusted == {"a": 0.004, "b": 0.06, "c": 0.08, "d": 0.5}


def test_contract_parse_chain_accepts_int_and_half_scores() -> None:
    contract = ScoringContract(
        channels=(
            Channel(key="content", label="Content"),
            Channel(key="organization", label="Organization"),
            Channel(key="language", label="Language"),
        ),
        grid_min_x2=1,
        grid_max_x2=10,
    )

    def parse(value):
        body = json.dumps({"content": value, "organization": 2.5, "language": 2.5})
        try:
            parsed = contract.score_model.model_validate_json(body)
            return contract.validate_scores(dict(parsed.model_dump(mode="json")))[
                "content"
            ]
        except Exception:
            return None

    assert parse(1) == 2  # JSON integer scores are legal
    assert parse(3) == 6
    assert parse(2.5) == 5
    assert parse(0.5) == 1
    assert parse(0.0) is None  # off-grid rejected
    assert parse(5.5) is None
    assert parse("2.5") is None  # strings rejected
