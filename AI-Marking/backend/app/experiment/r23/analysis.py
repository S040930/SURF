"""Pre-registered r23 metrics and reproducible clustered inference."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy.stats import spearmanr

from app.experiment.r23 import PROTOCOL_ID, SAMPLING_SEED
from app.experiment.r23.protocol import BOOTSTRAP_REPLICATES

CHANNELS = ("content", "organization", "language")


@dataclass(frozen=True, slots=True)
class AnalysisRow:
    model_binding_id: str
    dimension: str
    cluster_id: str
    prompt_sha256: str
    input_sha256: str
    label_x2: int
    content_x2: int
    organization_x2: int
    language_x2: int
    word_count: int

    def score(self, channel: str) -> int:
        return int(getattr(self, f"{channel}_x2"))


def _cluster(rows: Iterable[AnalysisRow]) -> dict[str, list[AnalysisRow]]:
    result: dict[str, list[AnalysisRow]] = defaultdict(list)
    for row in rows:
        result[row.cluster_id].append(row)
    return result


def monotonic_pair_accuracy(rows: Sequence[AnalysisRow], channel: str) -> float:
    """Macro-cluster MPA: correct=1, reverse=0, tie=0.5.

    Identical-input cross-level pairs are excluded because they contain no actual
    textual contrast. Corruption repeats remain independent observations.
    """
    cluster_scores: list[float] = []
    for values in _cluster(rows).values():
        credits: list[float] = []
        for index, left in enumerate(values):
            for right in values[index + 1 :]:
                if (
                    left.label_x2 == right.label_x2
                    or left.input_sha256 == right.input_sha256
                ):
                    continue
                low, high = (
                    (left, right) if left.label_x2 < right.label_x2 else (right, left)
                )
                low_score, high_score = low.score(channel), high.score(channel)
                credits.append(
                    1.0
                    if high_score > low_score
                    else 0.5 if high_score == low_score else 0.0
                )
        if credits:
            cluster_scores.append(float(np.mean(credits)))
    return float(np.mean(cluster_scores)) if cluster_scores else math.nan


def quadratic_weighted_kappa(
    labels_x2: Sequence[int], scores_x2: Sequence[int]
) -> float:
    if len(labels_x2) != len(scores_x2) or not labels_x2:
        return math.nan
    observed = np.zeros((9, 9), dtype=float)
    for label, score in zip(labels_x2, scores_x2, strict=True):
        observed[label - 2, score - 2] += 1
    n = observed.sum()
    expected = np.outer(observed.sum(axis=1), observed.sum(axis=0)) / n
    indices = np.arange(9)
    weights = ((indices[:, None] - indices[None, :]) ** 2) / 64
    denominator = float((weights * expected).sum())
    if denominator == 0:
        return 1.0 if float((weights * observed).sum()) == 0 else math.nan
    return 1.0 - float((weights * observed).sum()) / denominator


def mae(rows: Sequence[AnalysisRow], channel: str) -> float:
    return float(np.mean([abs(row.score(channel) - row.label_x2) / 2 for row in rows]))


def rank_correlation(rows: Sequence[AnalysisRow], channel: str) -> float:
    if len(rows) < 2:
        return math.nan
    value = spearmanr(
        [row.label_x2 for row in rows],
        [row.score(channel) for row in rows],
    ).statistic
    return float(value) if not np.isnan(value) else math.nan


def extreme_level_difference(rows: Sequence[AnalysisRow], channel: str) -> float:
    low = [row.score(channel) / 2 for row in rows if row.label_x2 == 2]
    high = [row.score(channel) / 2 for row in rows if row.label_x2 == 10]
    if not low or not high:
        return math.nan
    return float(np.mean(high) - np.mean(low))


def score_slope(rows: Sequence[AnalysisRow], channel: str) -> float:
    if len(rows) < 2:
        return math.nan
    x = np.asarray([row.label_x2 / 2 for row in rows], dtype=float)
    y = np.asarray([row.score(channel) / 2 for row in rows], dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def selectivity_index(rows: Sequence[AnalysisRow]) -> float:
    return score_slope(rows, "organization") - float(
        np.mean([score_slope(rows, "content"), score_slope(rows, "language")])
    )


def adjacent_level_effects(
    rows: Sequence[AnalysisRow], channel: str
) -> list[dict[str, float | int]]:
    """Observed adjacent contrasts, excluding pairs with identical input text."""
    result: list[dict[str, float | int]] = []
    for low_label in range(2, 10):
        differences: list[float] = []
        for values in _cluster(rows).values():
            lows = [row for row in values if row.label_x2 == low_label]
            highs = [row for row in values if row.label_x2 == low_label + 1]
            for low in lows:
                for high in highs:
                    if low.input_sha256 != high.input_sha256:
                        differences.append(
                            (high.score(channel) - low.score(channel)) / 2
                        )
        result.append(
            {
                "from": low_label / 2,
                "to": (low_label + 1) / 2,
                "n_text_changed_pairs": len(differences),
                "mean_score_difference": (
                    float(np.mean(differences)) if differences else math.nan
                ),
            }
        )
    return result


def _level_trend(
    rows: Sequence[AnalysisRow],
    channel: str,
    *,
    replicates: int,
    namespace: str,
) -> list[dict[str, float | list[float]]]:
    groups = list(_cluster(rows).values())
    seed = int.from_bytes(
        hashlib.sha256(f"{SAMPLING_SEED}|trend|{namespace}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    boot = np.full((replicates, 9), np.nan, dtype=float)
    for replicate in range(replicates):
        picks = rng.integers(0, len(groups), size=len(groups))
        sample = [row for pick in picks for row in groups[int(pick)]]
        for offset, label in enumerate(range(2, 11)):
            scores = [row.score(channel) / 2 for row in sample if row.label_x2 == label]
            if scores:
                boot[replicate, offset] = float(np.mean(scores))
    result: list[dict[str, float | list[float]]] = []
    for offset, label in enumerate(range(2, 11)):
        observed = [row.score(channel) / 2 for row in rows if row.label_x2 == label]
        finite = boot[:, offset][np.isfinite(boot[:, offset])]
        low, high = np.quantile(finite, [0.025, 0.975])
        result.append(
            {
                "case_level": label / 2,
                "mean": float(np.mean(observed)),
                "ci95": [float(low), float(high)],
            }
        )
    return result


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values in original order."""
    n = len(p_values)
    ordered = sorted(range(n), key=lambda index: p_values[index])
    adjusted = [1.0] * n
    running = 0.0
    for rank, index in enumerate(ordered):
        candidate = min(1.0, (n - rank) * float(p_values[index]))
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def _bootstrap(
    rows: Sequence[AnalysisRow],
    metric: Callable[[Sequence[AnalysisRow]], float],
    *,
    replicates: int,
    seed_namespace: str,
    null: float,
    alternative: str = "greater",
) -> dict[str, float]:
    groups = list(_cluster(rows).values())
    if not groups:
        return {"low": math.nan, "high": math.nan, "p": math.nan}
    seed_bytes = hashlib.sha256(
        f"{SAMPLING_SEED}|{seed_namespace}".encode("utf-8")
    ).digest()[:8]
    rng = np.random.default_rng(int.from_bytes(seed_bytes, "big"))
    values = np.empty(replicates, dtype=float)
    for index in range(replicates):
        picks = rng.integers(0, len(groups), size=len(groups))
        sample = [row for pick in picks for row in groups[int(pick)]]
        values[index] = metric(sample)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {"low": math.nan, "high": math.nan, "p": math.nan}
    if alternative == "greater":
        p_value = (1 + int(np.sum(values <= null))) / (len(values) + 1)
    else:
        p_value = (1 + int(np.sum(np.abs(values) >= abs(metric(rows))))) / (
            len(values) + 1
        )
    low, high = np.quantile(values, [0.025, 0.975])
    return {"low": float(low), "high": float(high), "p": float(p_value)}


def _permutation_control(
    rows: Sequence[AnalysisRow], channel: str, *, repetitions: int
) -> dict[str, float]:
    grouped = list(_cluster(rows).values())
    rng = np.random.default_rng(SAMPLING_SEED + 23)
    mpas: list[float] = []
    kappas: list[float] = []
    for _ in range(repetitions):
        permuted: list[AnalysisRow] = []
        for values in grouped:
            labels = rng.permutation([row.label_x2 for row in values])
            permuted.extend(
                AnalysisRow(
                    model_binding_id=row.model_binding_id,
                    dimension=row.dimension,
                    cluster_id=row.cluster_id,
                    prompt_sha256=row.prompt_sha256,
                    input_sha256=row.input_sha256,
                    label_x2=int(label),
                    content_x2=row.content_x2,
                    organization_x2=row.organization_x2,
                    language_x2=row.language_x2,
                    word_count=row.word_count,
                )
                for row, label in zip(values, labels, strict=True)
            )
        mpas.append(monotonic_pair_accuracy(permuted, channel))
        kappas.append(
            quadratic_weighted_kappa(
                [row.label_x2 for row in permuted],
                [row.score(channel) for row in permuted],
            )
        )
    return {"mpa_mean": float(np.nanmean(mpas)), "qwk_mean": float(np.nanmean(kappas))}


def _language_length_baseline(rows: Sequence[AnalysisRow]) -> dict[str, float | int]:
    frame = pd.DataFrame(
        {
            "label": [row.label_x2 / 2 for row in rows],
            "log_words": [math.log1p(row.word_count) for row in rows],
            "prompt": [row.prompt_sha256 for row in rows],
        }
    )
    if frame.empty:
        return {"n": 0, "r_squared": math.nan, "mae": math.nan}
    fitted = smf.ols("label ~ log_words + C(prompt)", data=frame).fit()
    predictions = fitted.predict(frame).clip(1, 5)
    return {
        "n": int(len(frame)),
        "r_squared": float(fitted.rsquared),
        "mae": float(np.mean(np.abs(predictions - frame["label"]))),
    }


def build_report(
    rows: Sequence[AnalysisRow], *, bootstrap_replicates: int = BOOTSTRAP_REPLICATES
) -> dict:
    """Build the locked formal report; callers must enforce lifecycle embargo."""
    model_ids = sorted({row.model_binding_id for row in rows})
    if len(model_ids) not in {1, 2}:
        raise ValueError("r23 analysis requires one model binding")

    cells: list[dict] = []
    h1_p: list[float] = []
    h2_p: list[float] = []
    for model_id in model_ids:
        for dimension in CHANNELS:
            subset = [
                row
                for row in rows
                if row.model_binding_id == model_id and row.dimension == dimension
            ]
            mpa_value = monotonic_pair_accuracy(subset, dimension)
            qwk_value = quadratic_weighted_kappa(
                [row.label_x2 for row in subset],
                [row.score(dimension) for row in subset],
            )
            mpa_bootstrap = _bootstrap(
                subset,
                lambda sample, channel=dimension: monotonic_pair_accuracy(
                    sample, channel
                ),
                replicates=bootstrap_replicates,
                seed_namespace=f"h1|{model_id}|{dimension}",
                null=0.5,
            )
            qwk_bootstrap = _bootstrap(
                subset,
                lambda sample, channel=dimension: quadratic_weighted_kappa(
                    [row.label_x2 for row in sample],
                    [row.score(channel) for row in sample],
                ),
                replicates=bootstrap_replicates,
                seed_namespace=f"h2|{model_id}|{dimension}",
                null=0.0,
            )
            h1_p.append(mpa_bootstrap["p"])
            h2_p.append(qwk_bootstrap["p"])
            cells.append(
                {
                    "model_binding_id": model_id,
                    "dimension": dimension,
                    "evidence_level": (
                        "confirmatory"
                        if dimension == "organization"
                        else "secondary" if dimension == "content" else "exploratory"
                    ),
                    "n": len(subset),
                    "mpa": mpa_value,
                    "mpa_ci95": [mpa_bootstrap["low"], mpa_bootstrap["high"]],
                    "mpa_p_raw": mpa_bootstrap["p"],
                    "qwk_intended_label_recovery": qwk_value,
                    "qwk_ci95": [qwk_bootstrap["low"], qwk_bootstrap["high"]],
                    "qwk_p_raw": qwk_bootstrap["p"],
                    "mae": mae(subset, dimension),
                    "spearman_rho": rank_correlation(subset, dimension),
                    "extreme_level_difference": extreme_level_difference(
                        subset, dimension
                    ),
                    "trend": _level_trend(
                        subset,
                        dimension,
                        replicates=bootstrap_replicates,
                        namespace=f"{model_id}|{dimension}",
                    ),
                    "adjacent_level_effects": adjacent_level_effects(subset, dimension),
                    "permutation_negative_control": _permutation_control(
                        subset, dimension, repetitions=min(1_000, bootstrap_replicates)
                    ),
                }
            )

    h1_adjusted = holm_adjust(h1_p)
    h2_adjusted = holm_adjust(h2_p)
    for index, cell in enumerate(cells):
        cell["mpa_p_holm"] = h1_adjusted[index]
        cell["qwk_p_holm"] = h2_adjusted[index]
        cell["h1_pass"] = bool(cell["mpa"] > 0.5 and h1_adjusted[index] < 0.05)
        cell["h2_pass"] = bool(
            cell["qwk_intended_label_recovery"] > 0 and h2_adjusted[index] < 0.05
        )

    selectivity: list[dict] = []
    h3_p: list[float] = []
    for model_id in model_ids:
        subset = [
            row
            for row in rows
            if row.model_binding_id == model_id and row.dimension == "organization"
        ]
        si = selectivity_index(subset)
        bootstrap = _bootstrap(
            subset,
            selectivity_index,
            replicates=bootstrap_replicates,
            seed_namespace=f"h3|{model_id}",
            null=0.0,
        )
        means = [
            AnalysisRow(
                model_binding_id=row.model_binding_id,
                dimension=row.dimension,
                cluster_id=row.cluster_id,
                prompt_sha256=row.prompt_sha256,
                input_sha256=row.input_sha256,
                label_x2=row.label_x2,
                content_x2=round(
                    (row.content_x2 + row.organization_x2 + row.language_x2) / 3
                ),
                organization_x2=round(
                    (row.content_x2 + row.organization_x2 + row.language_x2) / 3
                ),
                language_x2=round(
                    (row.content_x2 + row.organization_x2 + row.language_x2) / 3
                ),
                word_count=row.word_count,
            )
            for row in subset
        ]
        h3_p.append(bootstrap["p"])
        selectivity.append(
            {
                "model_binding_id": model_id,
                "si": si,
                "ci95": [bootstrap["low"], bootstrap["high"]],
                "p_raw": bootstrap["p"],
                "slopes": {
                    channel: score_slope(subset, channel) for channel in CHANNELS
                },
                "mean_channel_negative_control_si": selectivity_index(means),
            }
        )
    h3_adjusted = holm_adjust(h3_p)
    for index, value in enumerate(selectivity):
        value["p_holm"] = h3_adjusted[index]
        value["h3_pass"] = bool(value["si"] > 0 and h3_adjusted[index] < 0.05)

    stable: dict[str, bool] = {}
    for dimension in CHANNELS:
        values = [cell for cell in cells if cell["dimension"] == dimension]
        stable[dimension] = bool(
            len(values) == 2
            and all(cell["h1_pass"] and cell["h2_pass"] for cell in values)
            and (
                dimension != "organization"
                or all(value["h3_pass"] for value in selectivity)
            )
        )

    language_rows = [row for row in rows if row.dimension == "language"]
    common_prompts = {
        prompt
        for prompt, values in _cluster_by_prompt(language_rows).items()
        if {row.label_x2 for row in values} == set(range(2, 11))
    }
    common_subset = [
        row for row in language_rows if row.prompt_sha256 in common_prompts
    ]
    return {
        "protocol_id": PROTOCOL_ID,
        "analysis_status": "locked",
        "bootstrap_replicates": bootstrap_replicates,
        "multiplicity": {
            "h1": f"Holm across {len(cells)} model-by-dimension cells",
            "h2": f"Holm across {len(cells)} model-by-dimension cells",
            "h3": (
                "No adjustment for one model"
                if len(model_ids) == 1
                else "Holm across two models"
            ),
        },
        "cells": cells,
        "organization_selectivity": selectivity,
        "stable_sensitivity": stable,
        "language_length_prompt_baseline": _language_length_baseline(language_rows),
        "language_common_prompt_sensitivity": {
            "prompt_count": len(common_prompts),
            "n": len(common_subset),
            "by_model": [
                {
                    "model_binding_id": model_id,
                    "mpa": monotonic_pair_accuracy(
                        [
                            row
                            for row in common_subset
                            if row.model_binding_id == model_id
                        ],
                        "language",
                    ),
                    "qwk_intended_label_recovery": quadratic_weighted_kappa(
                        [
                            row.label_x2
                            for row in common_subset
                            if row.model_binding_id == model_id
                        ],
                        [
                            row.language_x2
                            for row in common_subset
                            if row.model_binding_id == model_id
                        ],
                    ),
                }
                for model_id in model_ids
            ],
        },
        "interpretation_guardrail": (
            "QWK measures recovery of CASE preset labels, not agreement with human scores; "
            "results do not establish real-essay scoring accuracy. Language is exploratory "
            "because length and prompt are confounded."
        ),
    }


def _cluster_by_prompt(rows: Iterable[AnalysisRow]) -> dict[str, list[AnalysisRow]]:
    result: dict[str, list[AnalysisRow]] = defaultdict(list)
    for row in rows:
        result[row.prompt_sha256].append(row)
    return result


__all__ = [
    "AnalysisRow",
    "adjacent_level_effects",
    "build_report",
    "extreme_level_difference",
    "holm_adjust",
    "mae",
    "monotonic_pair_accuracy",
    "quadratic_weighted_kappa",
    "rank_correlation",
    "score_slope",
    "selectivity_index",
]
