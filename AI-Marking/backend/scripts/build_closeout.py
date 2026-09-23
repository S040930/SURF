"""Closeout analysis for the 2026-09-12 formal run — zero model calls.

Fixes the remaining statistical issues from the interpretation review:

1. Correct one-way sum-of-squares decomposition of essay-level signed bias
   (the earlier ``between/(between+within)`` mixed a between-prompt variance
   with a second moment around the overall mean and is retired here).
2. Luna−Terra ΔQWK / ΔMAE under **paired stratified** bootstrap (same index
   set for both models within strata, design weights travel with rows), plus a
   prompt-**cluster** bootstrap sensitivity check, and two-sided bootstrap
   p-values with Holm adjustment over the pre-declared family
   {ΔQWK, ΔMAE} × {content, organization, language} = 6 tests of the single
   inference question "Luna vs Terra in this run".
3. Weighting sensitivity for the headline estimates: design weights 1/π vs
   stratum weights N_h/n_h vs unweighted — with the explicit statement that
   the 17 zero-coverage essays (entire prompt cells, π = 0) are not recovered
   by any reweighting of observed data.

Writes ``closeout_report.json`` with full provenance (input hashes, seeds,
replicates, script hash).  Essays are never read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_analysis_v3 import prompt_mapping  # noqa: E402

from app.experiment.core.analysis import (  # noqa: E402
    quadratic_weighted_kappa,
    weighted_mae,
)
from app.experiment.core.analysis.bootstrap import (  # noqa: E402
    percentile_ci,
    rng_from_seed,
)

CHANNELS = ("content", "organization", "language")
GRID_MIN_X2, GRID_MAX_X2 = 1, 10
REPLICATES = 10_000
FAMILY = [
    ("content", "qwk"),
    ("content", "mae"),
    ("organization", "qwk"),
    ("organization", "mae"),
    ("language", "qwk"),
    ("language", "mae"),
]


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _weighted_qwk(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> float:
    return quadratic_weighted_kappa(
        a.tolist(),
        b.tolist(),
        min_x2=GRID_MIN_X2,
        max_x2=GRID_MAX_X2,
        weights=w.tolist(),
    )


def _weighted_mae(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> float:
    return float(weighted_mae(a.tolist(), b.tolist(), weights=w.tolist()))


def ss_decomposition(bias: np.ndarray, clusters: np.ndarray) -> dict[str, float | int]:
    """One-way SS decomposition of essay-level signed bias by prompt.

    SS_between = Σ_i n_i (ȳ_i − ȳ)²,  SS_within = Σ_ij (y_ij − ȳ_i)².
    The share is descriptive; it is not an explained-variance statistic and
    carries no causal reading.
    """
    overall = float(bias.mean())
    ss_between = 0.0
    ss_within = 0.0
    sizes = []
    for c in sorted(set(clusters.tolist())):
        mask = clusters == c
        n_i = int(mask.sum())
        sizes.append(n_i)
        diff = float(bias[mask].mean()) - overall
        ss_between += n_i * diff * diff
        ss_within += float(((bias[mask] - bias[mask].mean()) ** 2).sum())
    total = ss_between + ss_within
    return {
        "ss_between_score_points2": round(ss_between, 6),
        "ss_within_score_points2": round(ss_within, 6),
        "between_ss_share": round(ss_between / total, 4) if total > 0 else None,
        "prompt_count": len(sizes),
        "essays_per_prompt_median": float(np.median(sizes)),
        "method": "one-way SS decomposition of essay-level signed bias by prompt (descriptive)",
    }


def paired_resample_indices(strata: np.ndarray, rng) -> np.ndarray:
    parts = [np.where(strata == h)[0] for h in sorted(set(strata.tolist()))]
    return np.concatenate(
        [part[rng.integers(0, len(part), len(part))] for part in parts]
    )


def paired_stratified_diff(
    left_pred: np.ndarray,
    right_pred: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    strata: np.ndarray,
    *,
    metric: str,
    seed: str,
) -> dict[str, float | int | str]:
    """Same resampled index set applied to both models (paired by essay)."""

    def stat(idx: np.ndarray) -> float:
        w = weights[idx]
        if metric == "qwk":
            return _weighted_qwk(left_pred[idx], labels[idx], w) - _weighted_qwk(
                right_pred[idx], labels[idx], w
            )
        return _weighted_mae(left_pred[idx], labels[idx], w) - _weighted_mae(
            right_pred[idx], labels[idx], w
        )

    rng = rng_from_seed(seed)
    samples = np.empty(REPLICATES, dtype=float)
    for i in range(REPLICATES):
        samples[i] = stat(paired_resample_indices(strata, rng))
    interval = percentile_ci(samples)
    observed = stat(np.arange(len(labels)))
    n_le = int((samples <= 0).sum())
    n_ge = int((samples >= 0).sum())
    p_two = min(
        1.0, 2.0 * min((n_le + 1) / (REPLICATES + 1), (n_ge + 1) / (REPLICATES + 1))
    )
    return {
        "estimate": round(float(observed), 4),
        "ci_low": round(float(interval["low"]), 4),
        "ci_high": round(float(interval["high"]), 4),
        "p_two_sided_bootstrap": round(float(p_two), 5),
        "replicates": REPLICATES,
        "scheme": "paired-stratified-bootstrap (weights travel with rows)",
    }


def paired_cluster_diff(
    left_pred: np.ndarray,
    right_pred: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    clusters: np.ndarray,
    *,
    metric: str,
    seed: str,
) -> dict[str, float | int | str]:
    """Prompt-cluster resampling: whole prompts picked with replacement, both
    models' rows kept together.  Sensitivity check only (no p-values)."""
    unique = sorted(set(clusters.tolist()))
    index_by_cluster = {c: np.where(clusters == c)[0] for c in unique}
    rng = rng_from_seed(seed)

    def stat(idx: np.ndarray) -> float:
        w = weights[idx]
        if metric == "qwk":
            return _weighted_qwk(left_pred[idx], labels[idx], w) - _weighted_qwk(
                right_pred[idx], labels[idx], w
            )
        return _weighted_mae(left_pred[idx], labels[idx], w) - _weighted_mae(
            right_pred[idx], labels[idx], w
        )

    samples = np.empty(REPLICATES, dtype=float)
    for i in range(REPLICATES):
        picked = rng.integers(0, len(unique), len(unique))
        idx = np.concatenate([index_by_cluster[unique[j]] for j in picked])
        samples[i] = stat(idx)
    interval = percentile_ci(samples)
    return {
        "estimate": round(float(stat(np.arange(len(labels)))), 4),
        "ci_low": round(float(interval["low"]), 4),
        "ci_high": round(float(interval["high"]), 4),
        "replicates": REPLICATES,
        "clusters": len(unique),
        "scheme": "prompt-cluster-bootstrap (sensitivity check; no p-values)",
    }


def holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm–Bonferroni over a declared family; returns adjusted p-values."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (key, p) in enumerate(items):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[key] = round(float(running), 5)
    return adjusted


def weight_sensitivity(
    pred: np.ndarray, labels: np.ndarray, design_w: np.ndarray, stratum_w: np.ndarray
) -> dict[str, dict[str, float]]:
    def qwk(w):
        return round(_weighted_qwk(pred, labels, w), 4)

    def mae(w):
        return round(_weighted_mae(pred, labels, w), 4)

    ones = np.ones_like(design_w)
    return {
        "design_1_over_pi": {"qwk": qwk(design_w), "mae": mae(design_w)},
        "stratum_N_over_n": {"qwk": qwk(stratum_w), "mae": mae(stratum_w)},
        "unweighted": {"qwk": qwk(ones), "mae": mae(ones)},
        "note": (
            "stratum 权重 N_h/n_h 把被覆盖样本当作代表整层的样本使用，是敏感性对照而非修复："
            "17 篇零覆盖作文属于完整题目 cell（π=0），对已观测数据重加权无法恢复它们，"
            "且缺失按题目整簇发生，代表性未知；只有新的抽样设计能覆盖。"
        ),
    }


def main(results_path: Path, dataset_root: Path, output_dir: Path) -> None:
    with results_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    primary = {
        (r["model"], r["input_sha256"]): r for r in rows if r["run_index"] == "0"
    }
    models = sorted({m for m, _ in primary})
    keys = sorted({k for _, k in primary})
    prompt_of = prompt_mapping(dataset_root, set(keys))
    prompt_tag = {
        k: "prompt:" + hashlib.sha256(prompt_of[k].encode()).hexdigest()[:10]
        for k in keys
    }

    left_rows = [primary[(models[0], k)] for k in keys]
    right_rows = [primary[(models[1], k)] for k in keys]
    # both models share each input's stratum and design weight (verify)
    assert all(
        lf["stratum"] == rt["stratum"] and lf["weight"] == rt["weight"]
        for lf, rt in zip(left_rows, right_rows)
    ), "paired inputs drifted"
    strata = np.array([int(r["stratum"]) for r in left_rows])
    design_w = np.array([float(r["weight"]) for r in left_rows])
    clusters = np.array([prompt_tag[k] for k in keys])

    # stratum weights N_h / n_h (sensitivity only, see note in weight_sensitivity)
    frame_per_stratum = {1: 278, 2: 498, 3: 572, 4: 424, 5: 194}
    sampled_per_stratum = {h: int((strata == h).sum()) for h in range(1, 6)}
    stratum_w = np.array(
        [frame_per_stratum[h] / sampled_per_stratum[h] for h in strata]
    )

    model_comparison: dict[str, dict[str, object]] = defaultdict(dict)
    for channel in CHANNELS:
        labels = np.array(
            [int(left_rows[i][f"label_{channel}"]) for i in range(len(keys))]
        )
        left_pred = np.array(
            [int(left_rows[i][f"prediction_{channel}"]) for i in range(len(keys))]
        )
        right_pred = np.array(
            [int(right_rows[i][f"prediction_{channel}"]) for i in range(len(keys))]
        )
        for metric in ("qwk", "mae"):
            model_comparison[channel][f"delta_{metric}"] = {
                "stratified": paired_stratified_diff(
                    left_pred,
                    right_pred,
                    labels,
                    design_w,
                    strata,
                    metric=metric,
                    seed=f"closeout|strat|{channel}|{metric}",
                ),
                "cluster_sensitivity": paired_cluster_diff(
                    left_pred,
                    right_pred,
                    labels,
                    design_w,
                    clusters,
                    metric=metric,
                    seed=f"closeout|cluster|{channel}|{metric}",
                ),
            }
    raw_p = {
        f"{channel}|{metric}": model_comparison[channel][f"delta_{metric}"][
            "stratified"
        ]["p_two_sided_bootstrap"]
        for channel, metric in FAMILY
    }
    adjusted = holm_adjust(raw_p)
    for key, p_adj in adjusted.items():
        channel, metric = key.split("|")
        model_comparison[channel][f"delta_{metric}"]["p_holm_adjusted"] = p_adj

    model_comparison = dict(model_comparison)

    variance: dict[str, dict[str, object]] = {}
    for model in models:
        variance[model] = {}
        mrows = [primary[(model, k)] for k in keys]
        for channel in CHANNELS:
            bias = np.array(
                [
                    (int(r[f"prediction_{channel}"]) - int(r[f"label_{channel}"])) / 2
                    for r in mrows
                ]
            )
            variance[model][channel] = ss_decomposition(bias, clusters)

    coverage_sensitivity: dict[str, object] = {}
    for model in models:
        mrows = [primary[(model, k)] for k in keys]
        coverage_sensitivity[model] = {
            channel: weight_sensitivity(
                np.array([int(r[f"prediction_{channel}"]) for r in mrows]),
                np.array([int(r[f"label_{channel}"]) for r in mrows]),
                design_w,
                stratum_w,
            )
            for channel in CHANNELS
        }

    report = {
        "analysis_version": "closeout_v1",
        "source": {
            "results_csv_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
            "dataset_tsv_sha256": "901c4b505cfd57b3ff4dace2926c0a02423a692cac335475311487162509d140",
            "primary_rows": len(keys),
            "models": models,
            "no_model_calls": True,
        },
        "family_definition": {
            "inference_question": "本次运行内 Luna vs Terra 的模型比较",
            "family": [f"Δ{m.upper()}·{c}" for c, m in FAMILY],
            "family_size": len(FAMILY),
            "correction": "Holm–Bonferroni（校正仅适用于本族；跨运行比较仅作描述性复制，不入族）",
        },
        "model_comparison": model_comparison,
        "prompt_variance": {
            "note": (
                "对essay级 signed bias（分）按题目做 one-way SS 分解；比例为描述性，"
                "不是解释率，也不支持因果表述。早期 29%–33% 的比例混用了"
                "“相对总体均值的二阶矩”，已废弃。"
            ),
            "per_model": variance,
        },
        "coverage_sensitivity": {
            "frame_per_stratum": frame_per_stratum,
            "sampled_per_stratum": sampled_per_stratum,
            "zero_coverage_units_total": 17,
            "per_model": coverage_sensitivity,
        },
        "provenance": {
            "script": "backend/scripts/build_closeout.py",
            "script_sha256": _canonical_sha256(
                Path(__file__).read_text(encoding="utf-8")
            ),
            "seeds": [f"closeout|strat|{c}|{m}" for c, m in FAMILY]
            + [f"closeout|cluster|{c}|{m}" for c, m in FAMILY],
            "replicates": REPLICATES,
            "bootstrap_ci": "percentile, 2.5/97.5",
            "p_values": "two-sided bootstrap with +1 smoothing, Holm-adjusted over the declared family",
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "closeout_report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print("written:", output_dir / "closeout_report.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--dataset-root", type=Path, default=Path("/Users/mac/Desktop/SURF/DREsS")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    main(args.results, args.dataset_root, args.output_dir)
