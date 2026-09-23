"""Regression guard for the primary→retest pairing correction."""

import csv

from scripts.build_analysis_v2 import _agreement, build


def test_retest_agreement_uses_two_predictions_not_the_archived_label():
    # Archived labels deliberately differ from both predictions.  If the old
    # label-vs-second-score implementation returns, this fixture will fail.
    primary_predictions = [4, 6, 8, 10]
    retest_predictions = [4, 6, 8, 10]
    agreement = _agreement(primary_predictions, retest_predictions, seed="regression")
    assert agreement["qwk"]["estimate"] == 1.0
    assert agreement["mae"]["estimate"] == 0.0
    assert agreement["exact_rate"] == 1.0


def test_report_pairs_primary_and_retest_predictions_not_labels(tmp_path):
    fields = [
        "template_id",
        "model",
        "input_sha256",
        "run_index",
        *[f"label_{channel}" for channel in ("content", "organization", "language")],
        *[
            f"prediction_{channel}"
            for channel in ("content", "organization", "language")
        ],
    ]
    path = tmp_path / "results.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run_index, score in (("0", 8), ("1", 8)):
            writer.writerow(
                {
                    "template_id": "fixture",
                    "model": "model-a",
                    "input_sha256": "input-a",
                    "run_index": run_index,
                    **{
                        f"label_{channel}": 2
                        for channel in ("content", "organization", "language")
                    },
                    **{
                        f"prediction_{channel}": score
                        for channel in ("content", "organization", "language")
                    },
                }
            )
    report = build(path)
    assert report["test_retest"]["model-a"]["organization"]["qwk"]["estimate"] == 1.0
    assert (
        report["criterion_agreement"]["model-a"]["organization"]["qwk"]["estimate"]
        < 1.0
    )
