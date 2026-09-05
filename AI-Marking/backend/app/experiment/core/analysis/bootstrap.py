"""Seeded bootstrap machinery for descriptive comparisons."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import TypeVar

import numpy as np

T = TypeVar("T")


def rng_from_seed(seed: str) -> np.random.Generator:
    """Derive a platform-stable generator from a string seed."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def percentile_ci(
    values: Sequence[float] | np.ndarray, *, level: float = 0.95
) -> dict[str, float]:
    if len(values) == 0:
        raise ValueError("cannot bootstrap an empty distribution")
    alpha = (1.0 - level) / 2.0
    low, high = np.percentile(values, [100 * alpha, 100 * (1.0 - alpha)])
    return {"low": float(low), "high": float(high)}


def paired_bootstrap_diff(
    *,
    left: Callable[[np.ndarray, np.ndarray, np.ndarray], float],
    right: Callable[[np.ndarray, np.ndarray, np.ndarray], float],
    left_labels: Sequence[float],
    left_scores: Sequence[float],
    right_labels: Sequence[float],
    right_scores: Sequence[float],
    weights: Sequence[float],
    replicates: int,
    seed: str,
    level: float = 0.95,
) -> dict[str, float | int]:
    """Bootstrap the difference ``metric(left) - metric(right)`` over inputs.

    Inputs are paired positionally: both models must be scored on the same
    items in the same order.  Each replicate resamples input rows with
    replacement and recomputes both weighted metrics on the same resample.
    """
    left_labels_arr = np.asarray(left_labels, dtype=float)
    left_scores_arr = np.asarray(left_scores, dtype=float)
    right_labels_arr = np.asarray(right_labels, dtype=float)
    right_scores_arr = np.asarray(right_scores, dtype=float)
    weight_arr = np.asarray(weights, dtype=float)
    sizes = {
        left_labels_arr.shape,
        left_scores_arr.shape,
        right_labels_arr.shape,
        right_scores_arr.shape,
        weight_arr.shape,
    }
    if len(sizes) != 1 or left_labels_arr.ndim != 1 or left_labels_arr.size == 0:
        raise ValueError("paired bootstrap inputs must be non-empty and equal-length")
    observed = float(
        left(left_labels_arr, left_scores_arr, weight_arr)
        - right(right_labels_arr, right_scores_arr, weight_arr)
    )
    rng = rng_from_seed(seed)
    n = left_labels_arr.size
    diffs = np.empty(int(replicates), dtype=float)
    for index in range(int(replicates)):
        sample = rng.integers(0, n, n)
        diffs[index] = left(
            left_labels_arr[sample], left_scores_arr[sample], weight_arr[sample]
        ) - right(
            right_labels_arr[sample], right_scores_arr[sample], weight_arr[sample]
        )
    interval = percentile_ci(diffs, level=level)
    return {
        "observed_diff": observed,
        "ci_low": interval["low"],
        "ci_high": interval["high"],
        "replicates": int(replicates),
    }


__all__ = ["paired_bootstrap_diff", "percentile_ci", "rng_from_seed"]
