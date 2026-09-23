"""Create a non-destructive ``analysis_v2`` correction for a locked v1 export.

The old report used the archived teacher label for the retest section.  This
tool instead pairs primary and independent rerun predictions by
``(model, input_sha256)``.  It never reads essays and writes only a new report
directory, leaving the locked v1 artefacts unchanged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

import numpy as np

from app.experiment.core.analysis import (
    exact_agreement_rate,
    quadratic_weighted_kappa,
    weighted_mae,
    within_agreement_rate,
)
from app.experiment.core.analysis.bootstrap import percentile_ci, rng_from_seed

CHANNELS = ("content", "organization", "language")
GRID_MIN_X2, GRID_MAX_X2 = 1, 10
REPLICATES = 10_000


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _bootstrap_ci(
    left: list[int],
    right: list[int],
    metric: Callable[[list[int], list[int]], float],
    *,
    seed: str,
) -> dict[str, float | int]:
    if not left or len(left) != len(right):
        raise ValueError("bootstrap inputs must be non-empty paired vectors")
    rng = rng_from_seed(seed)
    a, b = np.asarray(left), np.asarray(right)
    samples = np.empty(REPLICATES, dtype=float)
    for index in range(REPLICATES):
        selected = rng.integers(0, len(a), len(a))
        samples[index] = metric(a[selected].tolist(), b[selected].tolist())
    interval = percentile_ci(samples)
    return {
        "estimate": metric(left, right),
        "ci_low": interval["low"],
        "ci_high": interval["high"],
        "replicates": REPLICATES,
    }


def _agreement(left: list[int], right: list[int], *, seed: str) -> dict[str, object]:
    def qwk(a: list[int], b: list[int]) -> float:
        return quadratic_weighted_kappa(a, b, min_x2=GRID_MIN_X2, max_x2=GRID_MAX_X2)

    mae = weighted_mae
    return {
        "qwk": _bootstrap_ci(left, right, qwk, seed=f"{seed}|qwk"),
        "mae": _bootstrap_ci(left, right, mae, seed=f"{seed}|mae"),
        "signed_bias": _bootstrap_ci(
            left,
            right,
            lambda a, b: sum((y - x) / 2 for x, y in zip(a, b)) / len(a),
            seed=f"{seed}|bias",
        ),
        "exact_rate": exact_agreement_rate(left, right),
        "within_half_point_rate": within_agreement_rate(left, right, tolerance_x2=1),
    }


def build(results_path: Path) -> dict[str, object]:
    with results_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    primary: dict[tuple[str, str], dict[str, str]] = {}
    retest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["model"], row["input_sha256"])
        (primary if row["run_index"] == "0" else retest)[key] = row

    criterion: dict[str, dict[str, object]] = defaultdict(dict)
    reliability: dict[str, dict[str, object]] = defaultdict(dict)
    by_model = sorted({row["model"] for row in rows})
    for model in by_model:
        model_primary = [row for (name, _), row in primary.items() if name == model]
        paired = [
            (first, retest[key])
            for key, first in primary.items()
            if key[0] == model and key in retest
        ]
        for channel in CHANNELS:
            labels = [int(row[f"label_{channel}"]) for row in model_primary]
            scores = [int(row[f"prediction_{channel}"]) for row in model_primary]
            criterion[model][channel] = _agreement(
                labels, scores, seed=f"criterion|{model}|{channel}"
            )
            first = [int(row[f"prediction_{channel}"]) for row, _ in paired]
            second = [int(row[f"prediction_{channel}"]) for _, row in paired]
            reliability[model][channel] = {
                **_agreement(first, second, seed=f"retest|{model}|{channel}"),
                "pair_count": len(paired),
            }

    return {
        "analysis_version": "analysis_v2",
        "source": {
            "results_csv_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
            "source_template_id": rows[0]["template_id"] if rows else None,
            "source_rows": len(rows),
        },
        "correction": {
            "retest_pairing": "model + input_sha256; primary prediction vs independent retest prediction",
            "superseded_error": "v1 retest compared the second prediction to the archived expert label",
            "score_contract": "1.0–5.0, step 0.5 (stored x2=2..10 for v2 confirmation)",
        },
        "criterion_agreement": criterion,
        "test_retest": reliability,
        "interpretation": [
            "Stability and agreement with archived expert labels are distinct evidence axes.",
            "This correction is diagnostic and does not establish general scoring validity.",
            "The old supervised prompt+length baseline is not a fair zero-shot LLM competitor; it is retained only as a prompt-held-out diagnostic.",
        ],
    }


def main(results_path: Path, output_dir: Path) -> None:
    report = build(results_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report["report_sha256"] = _canonical_sha256(report)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "BIAS.md").write_text(
        "# analysis_v2 偏差说明\n\n"
        "v1 的复测段误将第二次模型评分再次与档案专家标签比较。analysis_v2 按 "
        "`model + input_sha256` 配对主评分与独立复评分，故该段现在度量模型自一致性。"
        "原始锁定产物未被修改。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    main(args.results, args.output_dir)
