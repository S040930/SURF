"""Generic agreement metrics on a half-point grid (channel-agnostic).

All x2 helpers take integer scores on ``[min_x2, max_x2]`` and raise on
out-of-grid values so that adapter or contract bugs surface immediately.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import stats


class MetricError(ValueError):
    """Raised when metric inputs are empty or off-grid."""


def _as_arrays(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    weights: Sequence[float] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    labels = np.asarray(labels_x2, dtype=float)
    scores = np.asarray(scores_x2, dtype=float)
    if labels.shape != scores.shape or labels.ndim != 1 or labels.size == 0:
        raise MetricError("labels and scores must be non-empty equal-length 1-D arrays")
    if weights is None:
        weight = np.ones_like(labels)
    else:
        weight = np.asarray(weights, dtype=float)
        if weight.shape != labels.shape or np.any(weight < 0):
            raise MetricError("weights must match the inputs and be non-negative")
    return labels, scores, weight


def _require_grid(values: np.ndarray, *, min_x2: int, max_x2: int, name: str) -> None:
    if np.any(values < min_x2) or np.any(values > max_x2):
        raise MetricError(f"{name} fall outside the [{min_x2}, {max_x2}] grid")


def quadratic_weighted_kappa(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    *,
    min_x2: int,
    max_x2: int,
    weights: Sequence[float] | None = None,
) -> float:
    """Quadratic weighted kappa over the integer categories min_x2..max_x2."""
    labels, scores, weight = _as_arrays(labels_x2, scores_x2, weights)
    _require_grid(labels, min_x2=min_x2, max_x2=max_x2, name="labels")
    _require_grid(scores, min_x2=min_x2, max_x2=max_x2, name="scores")
    categories = max_x2 - min_x2 + 1
    label_idx = labels.astype(int) - min_x2
    score_idx = scores.astype(int) - min_x2
    observed = np.zeros((categories, categories), dtype=float)
    np.add.at(observed, (label_idx, score_idx), weight)
    total = observed.sum()
    if total <= 0:
        raise MetricError("total weight must be positive")
    observed /= total
    row = observed.sum(axis=1, keepdims=True)
    column = observed.sum(axis=0, keepdims=True)
    expected = row @ column
    index = np.arange(categories)
    weight_matrix = (index[:, None] - index[None, :]) ** 2 / (categories - 1) ** 2
    denominator = float((weight_matrix * expected).sum())
    numerator = float((weight_matrix * observed).sum())
    if denominator == 0:
        return 1.0
    return float(1.0 - numerator / denominator)


def weighted_mae(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    weights: Sequence[float] | None = None,
) -> float:
    """Mean absolute error in score units (half-point differences / 2)."""
    labels, scores, weight = _as_arrays(labels_x2, scores_x2, weights)
    total = weight.sum()
    if total <= 0:
        raise MetricError("total weight must be positive")
    return float((np.abs(labels - scores) / 2.0 * weight).sum() / total)


def _weighted_rate(
    mask: np.ndarray, weight: np.ndarray
) -> float:
    total = weight.sum()
    if total <= 0:
        raise MetricError("total weight must be positive")
    return float((mask * weight).sum() / total)


def exact_agreement_rate(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    weights: Sequence[float] | None = None,
) -> float:
    labels, scores, weight = _as_arrays(labels_x2, scores_x2, weights)
    return _weighted_rate(labels == scores, weight)


def within_agreement_rate(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    *,
    tolerance_x2: int,
    weights: Sequence[float] | None = None,
) -> float:
    labels, scores, weight = _as_arrays(labels_x2, scores_x2, weights)
    return _weighted_rate(np.abs(labels - scores) <= tolerance_x2, weight)


def spearman_correlation(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
) -> float:
    """Unweighted Spearman correlation; reported as a sample-level diagnostic."""
    labels, scores, _ = _as_arrays(labels_x2, scores_x2, None)
    if np.std(labels) == 0 or np.std(scores) == 0:
        return float("nan")
    correlation = stats.spearmanr(labels, scores).statistic
    return float(correlation)


def calibration_curve(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    *,
    min_x2: int,
    max_x2: int,
    weights: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """Per predicted-score bin: weighted mean human label, count, and weight."""
    labels, scores, weight = _as_arrays(labels_x2, scores_x2, weights)
    _require_grid(scores, min_x2=min_x2, max_x2=max_x2, name="scores")
    points: list[dict[str, Any]] = []
    for score_x2 in range(min_x2, max_x2 + 1):
        mask = scores.astype(int) == score_x2
        bin_weight = float(weight[mask].sum())
        if bin_weight <= 0:
            continue
        points.append(
            {
                "score_x2": score_x2,
                "score": score_x2 / 2,
                "mean_label": float((labels[mask] * weight[mask]).sum() / bin_weight),
                "count": int(mask.sum()),
                "weight": bin_weight,
            }
        )
    return points


def transition_matrix(
    labels_x2: Sequence[int],
    scores_x2: Sequence[int],
    *,
    min_x2: int,
    max_x2: int,
) -> list[list[int]]:
    """Counts of (label row, prediction column) pairs on the grid."""
    labels, scores, _ = _as_arrays(labels_x2, scores_x2, None)
    _require_grid(labels, min_x2=min_x2, max_x2=max_x2, name="labels")
    _require_grid(scores, min_x2=min_x2, max_x2=max_x2, name="scores")
    categories = max_x2 - min_x2 + 1
    matrix = np.zeros((categories, categories), dtype=int)
    np.add.at(
        matrix,
        (labels.astype(int) - min_x2, scores.astype(int) - min_x2),
        1,
    )
    return matrix.tolist()


__all__ = [
    "MetricError",
    "calibration_curve",
    "exact_agreement_rate",
    "quadratic_weighted_kappa",
    "spearman_correlation",
    "transition_matrix",
    "weighted_mae",
    "within_agreement_rate",
]
