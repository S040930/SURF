"""Create a non-destructive ``analysis_v3`` addendum for the locked formal export.

Three corrections over v1/v2, all computed without any model call:

1. Score-grid forensics: which half-point values each model actually emitted,
   whether the "half-integer lattice" is a parsing artefact or model behaviour,
   and whether the stored predictions match the attempts' ``output_sha256``.
2. Calibration with corrected units (both axes in score points) plus a
   rebuild of the deterministic sampling plan to audit design weights and
   population coverage (zero-allocation prompt cells).
3. On the identical retest subset: criterion alignment vs test-retest
   stability, with stratified-bootstrap and prompt-cluster-bootstrap intervals.

Essays are never read; the dataset TSV is used only to recover the normalized
prompt identity (whitespace-collapsed prompt text hash) that the sampling plan
already keyed on.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

import numpy as np
from scipy import stats as scipy_stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.experiment.core.analysis import (  # noqa: E402
    calibration_curve,
    quadratic_weighted_kappa,
    weighted_mae,
)
from app.experiment.core.analysis.bootstrap import (  # noqa: E402
    percentile_ci,
    rng_from_seed,
)
from app.experiment.core.sampling import stable_key  # noqa: E402
from app.experiment.templates.dress_new import (  # noqa: E402
    DressNewAdapter,
    DressNewHumanAgreementTemplate,
    input_identity,
    is_low_tail,
    normalized_prompt,
)

CHANNELS = ("content", "organization", "language")
GRID_MIN_X2, GRID_MAX_X2 = 1, 10
REPLICATES = 10_000
FORMAL_SEED = "20260905"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _qwk(a: list[int], b: list[int]) -> float:
    return quadratic_weighted_kappa(a, b, min_x2=GRID_MIN_X2, max_x2=GRID_MAX_X2)


# ---------------------------------------------------------------------------
# task 1: score-grid forensics
# ---------------------------------------------------------------------------
def grid_forensics(rows: list[dict[str, str]]) -> dict[str, object]:
    per_model: dict[str, dict[str, object]] = defaultdict(dict)
    for model in sorted({row["model"] for row in rows}):
        model_rows = [row for row in rows if row["model"] == model]
        for channel in CHANNELS:
            values = [int(row[f"prediction_{channel}"]) for row in model_rows]
            counts = Counter(values)
            odd = sum(count for value, count in counts.items() if value % 2 == 1)
            sub_floor = [
                {
                    "input_sha256": row["input_sha256"],
                    "run_index": int(row["run_index"]),
                    "label_x2": int(row[f"label_{channel}"]),
                }
                for row in model_rows
                if int(row[f"prediction_{channel}"]) == GRID_MIN_X2
            ]
            per_model[model][channel] = {
                "distinct_x2_values": sorted(counts),
                "value_counts": {str(v): counts[v] for v in sorted(counts)},
                "odd_x2_share": round(odd / len(values), 4),
                "integer_x2_share": round(1 - odd / len(values), 4),
                "predictions_at_grid_floor_x2": len(sub_floor),
                "grid_floor_rows": sub_floor,
                "min_x2": min(values),
                "max_x2": max(values),
            }
    return {
        "grid": "x2 in 1..10 (0.5–5.0 in 0.5 steps), strict pydantic enum at the CLI boundary",
        "parsing_chain": [
            "codex exec --output-schema enforces the JSON Schema enum {0.5, …, 5.0}",
            "CodexExecRunner re-validates the result file with the same pydantic model (strict float)",
            "ScoringContract.validate_scores maps value -> int(round(value * 2)); no other coercion exists",
            "no 'invalid_score_value'/'invalid_score_schema' attempt succeeded or was retried silently",
        ],
        "per_model": dict(per_model),
        "reading": (
            "The half-integer lattice is emitted by the model, not introduced by parsing: "
            "Luna places every single prediction on odd x2 values (0.5/1.5/2.5/3.5/4.5 分) "
            "and never uses an integer score, while Terra mixes integers and half-integers "
            "under the identical schema and prompt envelope. Two contract-side invites "
            "plausibly shape this behaviour: the frozen v1 contract starts at grid_min_x2=1 "
            "(0.5 分, below the DREsS rubric floor of 1.0 and below the v2 contract 1.0–5.0), "
            "and the label-blind envelope's literal example is {…: 0.5}, the grid minimum."
        ),
    }


def attempt_sha_integrity(
    results_path: Path, project_id: str, dsn: str
) -> dict[str, object]:
    """Recompute each succeeded attempt's output hash from the CSV predictions."""
    with results_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    expected: dict[tuple[str, str], str] = {}
    for row in rows:
        value = {channel: int(row[f"prediction_{channel}"]) / 2 for channel in CHANNELS}
        expected[(row["model"], row["input_sha256"], row["run_index"])] = (
            _canonical_sha256(value)
        )
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select requested_model, input_sha256, status, output_sha256, "
                "coalesce(error_code, '') from exp_call_attempts where project_id = %s",
                (project_id,),
            )
            attempts = cur.fetchall()
    finally:
        conn.close()
    succeeded = [a for a in attempts if a[2] == "succeeded"]
    # run_index is not stored on attempts; match each attempt's stored output
    # hash against the hashes implied by that (model, input)'s CSV predictions.
    by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (model, key, run_index), sha in expected.items():
        by_key[(model, key)].add(sha)
    matched = sum(1 for a in succeeded if a[3] in by_key[(a[0], a[1])])
    unmatched = [a for a in succeeded if a[3] not in by_key[(a[0], a[1])]]
    return {
        "status": "checked",
        "attempts_total": len(attempts),
        "succeeded": len(succeeded),
        "output_sha256_matched_csv_predictions": matched,
        "unmatched": len(unmatched),
        "failure_codes": dict(Counter(a[4] for a in attempts if a[2] != "succeeded")),
    }


# ---------------------------------------------------------------------------
# task 2: calibration units + sampling weights / coverage
# ---------------------------------------------------------------------------
def calibration_fixed(rows: list[dict[str, str]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for model in sorted({row["model"] for row in rows if row["run_index"] == "0"}):
        model_rows = [
            row for row in rows if row["model"] == model and row["run_index"] == "0"
        ]
        channels: dict[str, object] = {}
        for channel in CHANNELS:
            labels = [int(row[f"label_{channel}"]) for row in model_rows]
            preds = [int(row[f"prediction_{channel}"]) for row in model_rows]
            weights = [float(row["weight"]) for row in model_rows]
            points = calibration_curve(
                labels, preds, min_x2=GRID_MIN_X2, max_x2=GRID_MAX_X2, weights=weights
            )
            for point in points:
                point["mean_label_score_points"] = point["mean_label"] / 2
                point["score_is_half_integer"] = point["score_x2"] % 2 == 1
            channels[channel] = {
                "unit_note": (
                    "score = 模型评分（分）；mean_label_score_points = 加权专家均值（分）＝mean_label_x2/2。"
                    "锁定的 figures.zip 校准图把 x2 量表均值直接画在 0–5 轴上并截到 5.0，全部点被钉在顶端。"
                ),
                "points": points,
            }
        out[model] = channels
    return out


def calibration_svg(model: str, channel: str, points: list[dict[str, object]]) -> str:
    width, height = 460, 340
    left, top, pw, ph = 56.0, 30.0, 360.0, 260.0
    color = "#6366f1" if "luna" in model else "#14b8a6"

    def xat(v: float) -> float:
        return left + pw * (v - 0.5) / 4.5

    def yat(v: float) -> float:
        return top + ph * (1 - (v - 0.5) / 4.5)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="calibration {model} {channel}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="20" font-size="13" fill="#0f172a">校准（单位：分）— {model} · {channel}</text>',
    ]
    for v in (1.0, 2.0, 3.0, 4.0, 5.0):
        parts.append(
            f'<line x1="{xat(v):.1f}" y1="{yat(v):.1f}" x2="{xat(5.0):.1f}" y2="{yat(v):.1f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<line x1="{xat(v):.1f}" y1="{yat(v):.1f}" x2="{xat(v):.1f}" y2="{yat(0.5):.1f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{left - 8:.1f}" y="{yat(v) + 3.5:.1f}" font-size="10" fill="#64748b" text-anchor="end">{v:g}</text>'
        )
        parts.append(
            f'<text x="{xat(v):.1f}" y="{top + ph + 16:.1f}" font-size="10" fill="#64748b" text-anchor="middle">{v:g}</text>'
        )
    parts.append(
        f'<line x1="{xat(1.0):.1f}" y1="{yat(1.0):.1f}" x2="{xat(5.0):.1f}" y2="{yat(5.0):.1f}" stroke="#94a3b8" stroke-dasharray="4 4"/>'
    )
    max_count = max(int(p["count"]) for p in points)
    for p in points:
        x, y = xat(float(p["score"])), yat(float(p["mean_label_score_points"]))
        r = 4 + 8 * (int(p["count"]) / max_count) ** 0.5
        tip = (
            f'<title>模型 {p["score"]:g} 分（n={p["count"]}）→ 专家均值 '
            f'{p["mean_label_score_points"]:.2f} 分</title>'
        )
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" fill-opacity="0.75" '
            f'stroke="#0f172a" stroke-width="0.6">{tip}</circle>'
        )
    parts.append("</svg>")
    return "".join(parts)


def weight_audit(manifest_path: Path, dataset_root: Path) -> dict[str, object]:
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    audit = DressNewAdapter().audit(dataset_root)
    template = DressNewHumanAgreementTemplate()
    items_by_key = {item.key: item for item in audit.items}
    manifest_inputs = {item["input_sha256"]: item for item in manifest["inputs"]}

    def plan_matches(plan) -> bool:
        rebuilt = {s.key: s for s in plan.selections}
        if set(rebuilt) != set(manifest_inputs):
            return False
        return all(
            rebuilt[key].stratum == item["stratum"]
            and abs(rebuilt[key].inclusion_probability - item["inclusion_probability"])
            <= 1e-12
            and abs(rebuilt[key].design_weight - item["design_weight"]) <= 1e-9
            and rebuilt[key].forced == item["forced"]
            for key, item in manifest_inputs.items()
        )

    # The locked v1 formal excluded its pilot's inputs; later repeats may not
    # exclude anything.  Detect the mode from the manifest itself.
    candidate_plans = [
        ("none", template.sampling_plan(kind="formal", audit=audit, excluded_keys=()))
    ]
    pilot_plan = template.sampling_plan(kind="pilot_run", audit=audit)
    pilot_keys = {s.key for s in pilot_plan.selections}
    candidate_plans.append(
        (
            "pilot",
            template.sampling_plan(
                kind="formal", audit=audit, excluded_keys=pilot_keys
            ),
        )
    )
    matched = [(mode, plan) for mode, plan in candidate_plans if plan_matches(plan)]
    if not matched:
        raise ValueError(
            "rebuilt sampling plan does not match the manifest in any mode"
        )
    exclusion_mode, formal_plan = matched[0]

    mismatches: list[str] = [] if plan_matches(formal_plan) else ["plan drift"]

    # zero-coverage cells: prompt cells over the NON-FORCED pool (the same
    # cells stratified_select allocates across) that received zero seats, so
    # every essay in them has pi = 0.  Cells containing forced essays can still
    # hide uncovered non-forced members, which is why the pool excludes forced
    # items exactly like the production allocator does.
    strata_frame: Counter[int] = Counter()
    excluded_keys = set() if exclusion_mode == "none" else pilot_keys
    excluded_by_stratum: Counter[int] = Counter()
    for item in items_by_key.values():
        strata_frame[stratum_of_item(item)] += 1
    for key in excluded_keys:
        excluded_by_stratum[stratum_of_item(items_by_key[key])] += 1
    zero_by_stratum = zero_allocation_cells(
        items_by_key, excluded_keys, formal_plan.selections
    )

    per_stratum: dict[str, object] = {}
    zero_units_total = 0
    for stratum in range(1, 6):
        stratum_selections = [
            sel for sel in formal_plan.selections if sel.stratum == stratum
        ]
        implied_n = sum(sel.design_weight for sel in stratum_selections)
        frame_n = strata_frame[stratum] - excluded_by_stratum[stratum]
        zero_cells = {
            _prompt_tag(cell): size
            for cell, size in zero_by_stratum.get(stratum, {}).items()
        }
        zero_units = sum(zero_cells.values())
        zero_units_total += zero_units
        per_stratum[str(stratum)] = {
            "sampled": len(stratum_selections),
            "forced": sum(1 for sel in stratum_selections if sel.forced),
            "sum_design_weights": round(implied_n, 4),
            "frame_after_exclusion": frame_n,
            "uncovered_units_in_zero_allocation_cells": zero_units,
            "zero_allocation_cells": zero_cells,
        }
    frame_total = sum(strata_frame.values()) - len(excluded_keys)
    return {
        "plan_rebuild_matches_manifest": not mismatches,
        "mismatches": mismatches[:10],
        "exclusion_mode": exclusion_mode,
        "sampling_frame": {
            "dataset_unique_inputs": audit.report["unique_inputs"],
            "excluded_inputs": len(excluded_keys),
            "formal_frame": frame_total,
        },
        "weight_definition": (
            "weight = 1/π，π 在「层 × 题目 cell」内计算（Hamilton 分配），"
            "故同一题目同层的作文权重相同；强制纳入尾部 π=1。"
        ),
        "per_stratum": per_stratum,
        "coverage": {
            "sum_design_weights": round(
                sum(sel.design_weight for sel in formal_plan.selections), 4
            ),
            "ht_target_population": frame_total,
            "uncovered_units_total": zero_units_total,
            "coverage_gap_share": round(zero_units_total / frame_total, 4),
            "reading": (
                "Σ(1/π) 是被覆盖总体的规模，小于完整抽样框：若干题目 cell 的 Hamilton "
                "分配为 0 席，其作文 π=0，任何加权点估计都触不到它们。"
                "层内 cell 级 1/π 权重在「被覆盖子总体」内无偏，但总体总量被低估；"
                "v2 确认性协议若改用层权 N_h/n_h 或合并极小 cell，可消除该缺口。"
            ),
        },
        "retest_coverage": {
            str(k): v
            for k, v in sorted(
                Counter(
                    stratum_of_item(items_by_key[k]) for k in formal_plan.retest_keys
                ).items()
            )
        },
    }


def zero_allocation_cells(
    items: dict[str, object], pilot_keys: set[str], selections
) -> dict[int, dict[str, int]]:
    """Prompt cells (non-forced pool, per stratum) that received zero seats.

    Mirrors ``stratified_select``: the allocator only sees non-forced items, so
    a cell that contains a forced essay can still hide uncovered non-forced
    members.  Returns ``{stratum: {cell_key: uncovered_unit_count}}``.
    """
    pool_cells: dict[int, Counter[str]] = defaultdict(Counter)
    for item in items.values():
        if item.key in pilot_keys or is_low_tail(item.labels_x2):
            continue
        pool_cells[stratum_of_item(item)][
            stable_key("cell", item.prompt_norm)[:32]
        ] += 1
    covered: dict[int, set[str]] = defaultdict(set)
    for sel in selections:
        if not sel.forced:
            covered[sel.stratum].add(sel.cell_key)
    return {
        stratum: {
            cell: size for cell, size in cells.items() if cell not in covered[stratum]
        }
        for stratum, cells in sorted(pool_cells.items())
    }


def stratum_of_item(item) -> int:
    from app.experiment.templates.dress_new import stratum_of

    return stratum_of(sum(item.labels_x2.values()))


def _prompt_tag(prompt_norm: str) -> str:
    return "prompt:" + hashlib.sha256(prompt_norm.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# task 3: matched retest-subset comparison with design-aware intervals
# ---------------------------------------------------------------------------
def _rows_for(
    results: list[dict[str, str]], model: str, run_index: str
) -> dict[str, dict[str, str]]:
    return {
        row["input_sha256"]: row
        for row in results
        if row["model"] == model and row["run_index"] == run_index
    }


def _boot_ci(
    metric: Callable[[list[int], list[int]], float],
    a: list[int],
    b: list[int],
    *,
    strata: list[int],
    seed: str,
    weighted_by: list[float] | None = None,
) -> dict[str, float | int]:
    """Stratified percentile bootstrap (resample within stratum, keep weights)."""
    rng = rng_from_seed(seed)
    a_arr, b_arr = np.asarray(a), np.asarray(b)
    strata_arr = np.asarray(strata)
    weight_arr = np.ones_like(a_arr) if weighted_by is None else np.asarray(weighted_by)
    index_by_stratum = [np.where(strata_arr == h)[0] for h in sorted(set(strata))]
    samples = np.empty(REPLICATES, dtype=float)

    def stat(idx: np.ndarray) -> float:
        if weighted_by is None:
            return metric(a_arr[idx].tolist(), b_arr[idx].tolist())
        return _weighted_metric(metric, a_arr[idx], b_arr[idx], weight_arr[idx])

    for i in range(REPLICATES):
        parts = [
            part[rng.integers(0, len(part), len(part))] for part in index_by_stratum
        ]
        samples[i] = stat(np.concatenate(parts))
    interval = percentile_ci(samples)
    return {
        "estimate": stat(np.arange(len(a_arr))),
        "ci_low": interval["low"],
        "ci_high": interval["high"],
        "replicates": REPLICATES,
        "scheme": (
            "stratified-bootstrap"
            if weighted_by is None
            else "stratified-bootstrap-weighted"
        ),
    }


def _cluster_boot_ci(
    metric: Callable[[list[int], list[int]], float],
    a: list[int],
    b: list[int],
    *,
    clusters: list[str],
    seed: str,
) -> dict[str, float | int]:
    """Prompt-cluster bootstrap: resample prompts, keep all their essays."""
    rng = rng_from_seed(seed)
    a_arr, b_arr = np.asarray(a), np.asarray(b)
    cluster_arr = np.asarray(clusters)
    unique = sorted(set(clusters))
    index_by_cluster = {c: np.where(cluster_arr == c)[0] for c in unique}
    samples = np.empty(REPLICATES, dtype=float)
    for i in range(REPLICATES):
        picked = rng.integers(0, len(unique), len(unique))
        idx = np.concatenate([index_by_cluster[unique[j]] for j in picked])
        samples[i] = metric(a_arr[idx].tolist(), b_arr[idx].tolist())
    interval = percentile_ci(samples)
    return {
        "estimate": metric(a, b),
        "ci_low": interval["low"],
        "ci_high": interval["high"],
        "replicates": REPLICATES,
        "clusters": len(unique),
        "scheme": "prompt-cluster-bootstrap",
    }


def _weighted_metric(
    metric: Callable[[list[int], list[int]], float],
    a: np.ndarray,
    b: np.ndarray,
    w: np.ndarray,
) -> float:
    """QWK/MAE with design weights under resampling (weights travel with rows)."""
    if metric is _qwk:
        return quadratic_weighted_kappa(
            a.tolist(),
            b.tolist(),
            min_x2=GRID_MIN_X2,
            max_x2=GRID_MAX_X2,
            weights=w.tolist(),
        )
    return float(weighted_mae(a.tolist(), b.tolist(), weights=w.tolist()))


def retest_subset_analysis(
    results: list[dict[str, str]],
    prompt_of: dict[str, str],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for model in sorted({row["model"] for row in results}):
        primary = _rows_for(results, model, "0")
        retest_rows = _rows_for(results, model, "1")
        keys = sorted(retest_rows)
        strata = [int(primary[k]["stratum"]) for k in keys]
        weights = [float(primary[k]["weight"]) for k in keys]
        prompts = [prompt_of[k] for k in keys]
        per_channel: dict[str, object] = {}
        for channel in CHANNELS:
            labels = [int(primary[k][f"label_{channel}"]) for k in keys]
            pred0 = [int(primary[k][f"prediction_{channel}"]) for k in keys]
            pred1 = [int(retest_rows[k][f"prediction_{channel}"]) for k in keys]
            crit_abs = [abs(p - lab) / 2 for p, lab in zip(pred0, labels)]
            stab_abs = [abs(p0 - p1) / 2 for p0, p1 in zip(pred0, pred1)]

            def _mean_diff(x: list[float], y: list[float]) -> float:
                return float(np.mean(np.asarray(x) - np.asarray(y)))

            wilcoxon = scipy_stats.wilcoxon(crit_abs, stab_abs, zero_method="wilcox")
            per_channel[channel] = {
                "n_essays": len(keys),
                "criterion_vs_label": {
                    "qwk": _boot_ci(
                        _qwk,
                        labels,
                        pred0,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|crit|uw",
                    ),
                    "qwk_weighted": _boot_ci(
                        _qwk,
                        labels,
                        pred0,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|crit|w",
                        weighted_by=weights,
                    ),
                    "qwk_cluster": _cluster_boot_ci(
                        _qwk,
                        labels,
                        pred0,
                        clusters=prompts,
                        seed=f"v3|{model}|{channel}|crit|cl",
                    ),
                    "mae": _boot_ci(
                        weighted_mae,
                        labels,
                        pred0,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|critmae|uw",
                    ),
                    "signed_bias": _boot_ci(
                        lambda a, b: float(
                            np.mean((np.asarray(b) - np.asarray(a)) / 2)
                        ),
                        labels,
                        pred0,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|critbias|uw",
                    ),
                },
                "retest_stability": {
                    "qwk": _boot_ci(
                        _qwk,
                        pred0,
                        pred1,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|stab|uw",
                    ),
                    "qwk_cluster": _cluster_boot_ci(
                        _qwk,
                        pred0,
                        pred1,
                        clusters=prompts,
                        seed=f"v3|{model}|{channel}|stab|cl",
                    ),
                    "mae": _boot_ci(
                        weighted_mae,
                        pred0,
                        pred1,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|stabmae|uw",
                    ),
                },
                "paired_abs_error": {
                    "mean_abs_error_vs_label": round(float(np.mean(crit_abs)), 4),
                    "mean_abs_error_run0_vs_run1": round(float(np.mean(stab_abs)), 4),
                    "paired_diff_stability_minus_criterion": _boot_ci(
                        _mean_diff,
                        stab_abs,
                        crit_abs,
                        strata=strata,
                        seed=f"v3|{model}|{channel}|pairdiff",
                    ),
                    "share_essays_stability_error_smaller": round(
                        float(
                            np.mean(
                                [1 if s < c else 0 for s, c in zip(stab_abs, crit_abs)]
                            )
                        ),
                        4,
                    ),
                    "wilcoxon_signed_rank_p": float(wilcoxon.pvalue),
                },
            }
        out[model] = {"n_essays": len(keys), "channels": per_channel}
    return out


def prompt_robustness(
    results: list[dict[str, str]], prompt_of: dict[str, str]
) -> dict[str, object]:
    primary = {
        (row["model"], row["input_sha256"]): row
        for row in results
        if row["run_index"] == "0"
    }
    models = sorted({model for model, _ in primary})
    sample_prompts: dict[str, set[str]] = defaultdict(set)
    for (_, key), row in primary.items():
        sample_prompts[row["model"]].add(prompt_of[key])
    per_model: dict[str, object] = {}
    for model in models:
        rows = [row for (name, _), row in primary.items() if name == model]
        by_prompt: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            by_prompt[prompt_of[row["input_sha256"]]].append(row)
        sizes = sorted(len(v) for v in by_prompt.values())
        channels: dict[str, object] = {}
        for channel in CHANNELS:
            stats_per_prompt = []
            for prompt, prows in sorted(by_prompt.items()):
                n = len(prows)
                bias = (
                    sum(
                        (int(r[f"prediction_{channel}"]) - int(r[f"label_{channel}"]))
                        / 2
                        for r in prows
                    )
                    / n
                )
                mae = (
                    sum(
                        abs(
                            int(r[f"prediction_{channel}"]) - int(r[f"label_{channel}"])
                        )
                        / 2
                        for r in prows
                    )
                    / n
                )
                stats_per_prompt.append(
                    {
                        "prompt": _prompt_tag(prompt),
                        "n": n,
                        "bias": round(bias, 3),
                        "mae": round(mae, 3),
                    }
                )
            biases = np.array([s["bias"] for s in stats_per_prompt])
            between = float(biases.var(ddof=1)) if len(biases) > 1 else 0.0
            overall_bias = float(
                np.mean(
                    [
                        (int(r[f"prediction_{channel}"]) - int(r[f"label_{channel}"]))
                        / 2
                        for r in rows
                    ]
                )
            )
            within = float(
                np.mean(
                    [
                        (
                            (
                                int(r[f"prediction_{channel}"])
                                - int(r[f"label_{channel}"])
                            )
                            / 2
                            - overall_bias
                        )
                        ** 2
                        for r in rows
                    ]
                )
            )
            worst = sorted(
                stats_per_prompt, key=lambda s: abs(s["bias"]), reverse=True
            )[:3]
            channels[channel] = {
                "prompt_count": len(stats_per_prompt),
                "per_prompt_bias_iqr": [
                    round(float(np.percentile(biases, 25)), 3),
                    round(float(np.percentile(biases, 75)), 3),
                ],
                "between_prompt_bias_variance": round(between, 4),
                "within_prompt_bias_variance": round(within, 4),
                "between_share_of_ms_error": (
                    round(between / (between + within), 4) if between + within else None
                ),
                "most_biased_prompts": worst,
            }
        per_model[model] = {
            "essays": len(rows),
            "distinct_prompts": len(by_prompt),
            "essays_per_prompt_min_median_max": [
                sizes[0],
                sizes[len(sizes) // 2],
                sizes[-1],
            ],
            "channels": channels,
        }
    return {
        "note": (
            "题目身份由数据集 TSV 按 whitespace 归一化的 prompt 文本恢复（与抽样 cell 同键）；"
            "正文未读取。抽样权重在「层 × 题目」内为常数，故题目聚类 bootstrap 与设计天然对齐。"
        ),
        "per_model": per_model,
    }


def prompt_mapping(dataset_root: Path, keys: set[str]) -> dict[str, str]:
    wanted = set(keys)
    found: dict[str, str] = {}
    import csv as _csv

    _csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with (dataset_root / "DREsS_New.tsv").open(newline="", encoding="utf-8") as handle:
        for row in _csv.DictReader(handle, delimiter="\t", quotechar='"'):
            if not row["essay"].strip():
                continue
            key = input_identity(row["prompt"], row["essay"])
            if key in wanted and key not in found:
                found[key] = normalized_prompt(row["prompt"])
    missing = wanted - set(found)
    if missing:
        raise ValueError(f"prompt mapping missed {len(missing)} inputs")
    return found


# ---------------------------------------------------------------------------
def main(
    results_path: Path,
    manifest_path: Path,
    dataset_root: Path,
    output_dir: Path,
    dsn: str,
    project_id: str,
) -> None:
    with results_path.open(newline="", encoding="utf-8") as handle:
        results = list(csv.DictReader(handle))
    primary_keys = {row["input_sha256"] for row in results if row["run_index"] == "0"}
    prompt_of = prompt_mapping(dataset_root, primary_keys)

    report: dict[str, object] = {
        "analysis_version": "analysis_v3",
        "source": {
            "results_csv_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
            "source_rows": len(results),
            "no_model_calls": True,
        },
        "task1_score_grid": grid_forensics(results),
        "task1_output_sha_integrity": attempt_sha_integrity(
            results_path, project_id, dsn
        ),
        "task2_calibration_fixed_units": calibration_fixed(results),
        "task2_weight_and_coverage_audit": weight_audit(manifest_path, dataset_root),
        "task3_retest_subset": retest_subset_analysis(results, prompt_of),
        "task3_prompt_robustness": prompt_robustness(results, prompt_of),
        "interpretation": [
            "半整数分档是模型在合法网格上的输出行为；解析链无取整或改写。",
            "Σ(1/π) 只覆盖被抽到题目的子总体；零分配 cell 的 17 篇不在加权点估计的总体里。",
            "复测子集上稳定性与人评对齐使用同一批输入、同一套层内 bootstrap 与题目聚类 bootstrap。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = output_dir / "figures"
    fig_dir.mkdir(exist_ok=True)
    for model, channels in report["task2_calibration_fixed_units"].items():  # type: ignore[union-attr]
        for channel, payload in channels.items():  # type: ignore[union-attr]
            svg = calibration_svg(model, channel, payload["points"])  # type: ignore[index]
            (fig_dir / f"calibration_fixed_{model}_{channel}.svg").write_text(
                svg, encoding="utf-8"
            )

    report["report_sha256"] = _canonical_sha256(report)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print("written:", output_dir / "report.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--dataset-root", type=Path, default=Path("/Users/mac/Desktop/SURF/DREsS")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dsn", default="host=localhost port=5511 dbname=ai_marking_experiment"
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="project id whose exp_call_attempts are checked",
    )
    args = parser.parse_args()
    main(
        args.results,
        args.manifest,
        args.dataset_root,
        args.output_dir,
        args.dsn,
        args.project_id,
    )
