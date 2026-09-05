"""Live-corpus reconciliation tests for the DREsS_New adapter and template.

These tests pin the pre-registered audit constants against the shipped
``DREsS_New.tsv`` (sha 901c4b50…) and verify the sampling plan is exactly
rebuildable.  They follow the r23 convention of running against the local
restricted root.
"""

from pathlib import Path

import pytest

from app.experiment.core.registry import get_dataset, get_template
from app.experiment.core.sampling import stable_key, stratified_select
from app.experiment.templates.dress_new import (
    DATASET_KEY,
    EXPECTED_LOW_TAIL,
    EXPECTED_STRATA_SIZES,
    EXPECTED_UNIQUE_INPUTS,
    PILOT_QUOTA_PER_STRATUM,
    RETEST_QUOTA,
    STRATUM_QUOTAS,
    stratum_of,
    TEMPLATE_ID,
    DressNewAdapter,
    DressNewHumanAgreementTemplate,
)

DRESS_ROOT = Path("/Users/mac/Desktop/SURF/DREsS")
DRESS_FILE = DRESS_ROOT / "DREsS_New.tsv"

pytestmark = pytest.mark.skipif(
    not DRESS_FILE.is_file(), reason="restricted DREsS corpus not present on this machine"
)


@pytest.fixture(scope="module")
def audit():
    adapter = DressNewAdapter()
    return adapter.audit(DRESS_ROOT)


def test_audit_report_pins_every_pre_registered_constant(audit):
    report = audit.report
    assert report["raw_rows"] == 2279
    assert report["empty_essay_rows"] == 300
    assert report["conflict_groups"] == 5
    assert report["conflict_rows"] == 10
    assert report["merged_duplicate_records"] == 3
    assert report["unique_inputs"] == EXPECTED_UNIQUE_INPUTS == 1966
    assert report["distinct_prompts"] == 51  # whitespace-normalized prompts
    assert report["low_tail_inputs"] == EXPECTED_LOW_TAIL == 96
    assert report["strata_sizes"] == EXPECTED_STRATA_SIZES
    assert report["datasets"][0]["sha256"].startswith("901c4b50")
    mismatches = report["total_column_mismatches"]
    assert mismatches["count"] == 2
    assert mismatches["in_scoring_pool"] == 1
    # id 885: file says 10.0, the three dimensions sum to 10.5.
    in_pool = [item for item in mismatches["detail"] if item["in_pool"]]
    assert in_pool == [
        {"source_id": "885", "in_pool": True, "file_total": 10.0, "recomputed_total": 10.5}
    ]


def test_audit_report_survives_json_round_trip(audit):
    import json

    restored = json.loads(json.dumps(audit.report))
    assert restored == audit.report


def test_materialize_returns_selected_inputs_with_matching_metadata(audit):
    adapter = DressNewAdapter()
    keys = [item.key for item in audit.items[:25]]
    materialized = adapter.materialize(DRESS_ROOT, audit, keys)
    assert sorted(item.key for item in materialized) == sorted(keys)
    by_key = {item.key: item for item in audit.items}
    for row in materialized:
        source = by_key[row.key]
        assert row.labels_x2 == source.labels_x2
        assert row.word_count == source.word_count
        assert len(row.essay.split()) == row.word_count


def test_pilot_plan_draws_30_non_tail_inputs_six_per_stratum(audit):
    template = DressNewHumanAgreementTemplate()
    plan = template.sampling_plan(kind="pilot_run", audit=audit)
    assert len(plan.selections) == 30
    assert plan.retest_keys == frozenset()
    strata: dict[int, int] = {}
    for selection in plan.selections:
        strata[selection.stratum] = strata.get(selection.stratum, 0) + 1
        assert not selection.forced
    assert strata == {1: 6, 2: 6, 3: 6, 4: 6, 5: 6}
    # Rebuildable.
    again = template.sampling_plan(kind="pilot_run", audit=audit)
    assert [s.key for s in again.selections] == [s.key for s in plan.selections]


def test_formal_plan_yields_720_with_forced_tail_and_exact_quotas(audit):
    template = DressNewHumanAgreementTemplate()
    pilot_plan = template.sampling_plan(kind="pilot_run", audit=audit)
    plan = template.sampling_plan(
        kind="formal", audit=audit, excluded_keys=[s.key for s in pilot_plan.selections]
    )
    assert len(plan.selections) == sum(STRATUM_QUOTAS.values()) == 720
    assert len(plan.retest_keys) == sum(RETEST_QUOTA.values()) == 72

    strata: dict[int, int] = {}
    forced_count = 0
    for selection in plan.selections:
        strata[selection.stratum] = strata.get(selection.stratum, 0) + 1
        forced_count += int(selection.forced)
    assert strata == STRATUM_QUOTAS
    assert forced_count == EXPECTED_LOW_TAIL == 96

    # Design identity: first-order inclusion probabilities summed over every
    # member of the formal pool equal the total quota. Cells are
    # (stratum, normalized prompt) pairs: forced pi=1; cell members pi=k/N
    # within their stratum, whether selected or not.
    from app.experiment.templates.dress_new import is_low_tail

    pilot_keys = {s.key for s in pilot_plan.selections}
    pool = [item for item in audit.items if item.key not in pilot_keys]
    cell_of_item = lambda item: stable_key("cell", item.prompt_norm)[:32]  # noqa: E731
    k_by_cell: dict[tuple[int, str], int] = {}
    for selection in plan.selections:
        if not selection.forced:
            key = (selection.stratum, selection.cell_key)
            k_by_cell[key] = k_by_cell.get(key, 0) + 1
    n_by_cell: dict[tuple[int, str], int] = {}
    for item in pool:
        if is_low_tail(item.labels_x2):
            continue
        stratum = sum(item.labels_x2.values())
        cell = (stratum_of(stratum), cell_of_item(item))
        n_by_cell[cell] = n_by_cell.get(cell, 0) + 1
    total_pi = 0.0
    for item in pool:
        if is_low_tail(item.labels_x2):
            total_pi += 1.0
        else:
            cell = (stratum_of(sum(item.labels_x2.values())), cell_of_item(item))
            total_pi += k_by_cell.get(cell, 0) / n_by_cell[cell]
    assert total_pi == pytest.approx(720.0)

    # Recorded pi values on selected items match k/N for their stratum cell.
    for selection in plan.selections:
        if selection.forced:
            assert selection.inclusion_probability == 1.0
        else:
            cell = (selection.stratum, selection.cell_key)
            assert selection.inclusion_probability == pytest.approx(
                k_by_cell[cell] / n_by_cell[cell]
            )

    # Pilot inputs are excluded from the formal pool.
    assert pilot_keys.isdisjoint({s.key for s in plan.selections})

    # Retest is stratified 15/18/20/12/7 over the formal selections.
    retest_by_key = {s.key: s for s in plan.selections}
    retest_strata: dict[int, int] = {}
    for key in plan.retest_keys:
        stratum = retest_by_key[key].stratum
        retest_strata[stratum] = retest_strata.get(stratum, 0) + 1
    assert retest_strata == RETEST_QUOTA

    # The whole plan is a pure function of the seed.
    again = template.sampling_plan(
        kind="formal", audit=audit, excluded_keys=[s.key for s in pilot_plan.selections]
    )
    assert [s.key for s in again.selections] == [s.key for s in plan.selections]
    assert again.retest_keys == plan.retest_keys


def test_registry_holds_the_registered_template():
    assert get_dataset(DATASET_KEY).key == DATASET_KEY
    template = get_template(TEMPLATE_ID)
    assert template.runner_count == 2
    assert template.require_runner_alignment is True


def test_report_is_deterministic_on_synthetic_predictions(audit, monkeypatch):
    from app.experiment.templates import dress_new as dress_new_module

    monkeypatch.setattr(dress_new_module, "BOOTSTRAP_REPLICATES", 30)
    template = DressNewHumanAgreementTemplate()
    plan = template.sampling_plan(kind="formal", audit=audit)

    def fake_rows(model: str, offset: int):
        rows = []
        for selection in plan.selections:
            item = next(audit_item for audit_item in audit.items if audit_item.key == selection.key)
            if offset == 0:
                predictions = dict(item.labels_x2)
            else:
                # Reflect one half-point away within the grid: never equal to
                # the label, never off-grid.
                predictions = {
                    channel: (
                        value - 1 if value > 1 else value + 1
                    )
                    for channel, value in item.labels_x2.items()
                }
            rows.append(
                {
                    "binding_id": "b",
                    "position": 1 if model == "model-a" else 2,
                    "model": model,
                    "group_key": "primary",
                    "run_index": 0,
                    "input_sha256": selection.key,
                    "prompt_sha256": item.prompt_sha256,
                    "word_count": item.word_count,
                    "stratum": selection.stratum,
                    "forced": selection.forced,
                    "inclusion_probability": selection.inclusion_probability,
                    "weight": selection.design_weight,
                    "labels_x2": dict(item.labels_x2),
                    "predictions_x2": predictions,
                }
            )
        return rows

    rows = fake_rows("model-a", 0) + fake_rows("model-b", -1)
    retest_rows = [
        dict(row, run_index=1) for row in rows if row["input_sha256"] in plan.retest_keys
    ]
    attempts = [
        {"status": "succeeded", "latency_ms": 100, "attempt_number": 1, "evaluation_id": index}
        for index in range(len(rows))
    ]
    project = {"template_id": TEMPLATE_ID, "kind": "formal"}
    report, figures = template.build_report(
        project=project, rows=rows, retest_rows=retest_rows, attempts=attempts
    )
    again_report, again_figures = template.build_report(
        project=project, rows=rows, retest_rows=retest_rows, attempts=attempts
    )
    from app.services.experiments import canonical_sha256

    assert canonical_sha256(report) == canonical_sha256(again_report)
    assert figures == again_figures
    assert len(figures) == 9  # 3 calibration + 6 retest transition heatmaps

    model_a = report["per_model"]["model-a"]
    assert model_a["count"] == 720
    # Predictions equal labels for model-a: perfect agreement.
    for channel in ("content", "organization", "language"):
        assert model_a["channels"][channel]["weighted_qwk"] == pytest.approx(1.0)
        assert model_a["channels"][channel]["exact_rate"] == pytest.approx(1.0)
    # model-b is one half-point off everywhere: exact rate 0, ±0.5 rate 1.
    model_b = report["per_model"]["model-b"]
    for channel in ("content", "organization", "language"):
        assert model_b["channels"][channel]["exact_rate"] == pytest.approx(0.0)
        assert model_b["channels"][channel]["within_half_point_rate"] == pytest.approx(1.0)
    assert report["model_comparison"]["content"]["mae_diff"]["observed_diff"] < 0
    assert "design" in report and report["statements"]
