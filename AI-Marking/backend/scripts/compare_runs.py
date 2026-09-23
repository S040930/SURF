"""Compare two locked exports of the same template (deterministic resample).

Reads the primary-run rows of two ``results.csv`` exports, reports sample
overlap, and scores same-model cross-run prediction agreement on the shared
inputs.  No model calls, no essay text.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.experiment.core.analysis import (  # noqa: E402
    exact_agreement_rate,
    quadratic_weighted_kappa,
    weighted_mae,
    within_agreement_rate,
)

CHANNELS = ("content", "organization", "language")
GRID_MIN_X2, GRID_MAX_X2 = 1, 10


def primary_rows(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    by_model: dict[str, dict[str, dict[str, str]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["run_index"] != "0":
                continue
            by_model.setdefault(row["model"], {})[row["input_sha256"]] = row
    return by_model


def compare(left_csv: Path, right_csv: Path) -> dict[str, object]:
    left, right = primary_rows(left_csv), primary_rows(right_csv)
    models = sorted(set(left) & set(right))
    out: dict[str, object] = {
        "left_csv_sha256": hashlib.sha256(left_csv.read_bytes()).hexdigest(),
        "right_csv_sha256": hashlib.sha256(right_csv.read_bytes()).hexdigest(),
        "per_model": {},
    }
    for model in models:
        shared = sorted(set(left[model]) & set(right[model]))
        channels: dict[str, object] = {}
        for channel in CHANNELS:
            a = [int(left[model][k][f"prediction_{channel}"]) for k in shared]
            b = [int(right[model][k][f"prediction_{channel}"]) for k in shared]
            channels[channel] = {
                "qwk": round(
                    quadratic_weighted_kappa(
                        a, b, min_x2=GRID_MIN_X2, max_x2=GRID_MAX_X2
                    ),
                    4,
                ),
                "mae": round(weighted_mae(a, b), 4),
                "exact_rate": round(exact_agreement_rate(a, b), 4),
                "within_half_point_rate": round(
                    within_agreement_rate(a, b, tolerance_x2=1), 4
                ),
            }
        weight_or_stratum_drift = sum(
            1
            for k in shared
            if left[model][k]["weight"] != right[model][k]["weight"]
            or left[model][k]["stratum"] != right[model][k]["stratum"]
        )
        label_drift = sum(
            1
            for k in shared
            if any(
                left[model][k][f"label_{c}"] != right[model][k][f"label_{c}"]
                for c in CHANNELS
            )
        )
        out["per_model"][model] = {
            "left_rows": len(left[model]),
            "right_rows": len(right[model]),
            "shared_inputs": len(shared),
            "only_in_left": len(set(left[model]) - set(right[model])),
            "only_in_right": len(set(right[model]) - set(left[model])),
            "weight_or_stratum_drift_on_shared": weight_or_stratum_drift,
            "label_drift_on_shared": label_drift,
            "channels": channels,
        }
    return out


def main(left_csv: Path, right_csv: Path, output: Path) -> None:
    report = compare(left_csv, right_csv)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print("written:", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--left", type=Path, required=True, help="earlier run results.csv"
    )
    parser.add_argument(
        "--right", type=Path, required=True, help="later run results.csv"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.left, args.right, args.output)
