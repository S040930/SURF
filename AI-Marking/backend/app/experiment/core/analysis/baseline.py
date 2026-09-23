"""Deterministic length-and-prompt heuristic baseline.

Explicitly a "word-count + prompt heuristic", not a stand-in for human
scoring: an unweighted OLS fit of ``log(word_count)`` plus prompt indicators,
evaluated with the same metrics as the models via seeded 10-fold
cross-validation.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def baseline_predictions(
    *,
    keys: Sequence[str],
    word_counts: Sequence[int],
    prompts: Sequence[str],
    labels_x2: Sequence[int],
    seed: str,
    n_folds: int = 10,
) -> np.ndarray:
    """Out-of-fold predictions (in score units) for every input, deterministically.

    Inputs are ordered by ``stable_rank`` and split into ``n_folds`` contiguous
    folds, so the split is a pure function of ``(seed, key)``.  Each held-out
    fold is predicted by an OLS fit on the remaining folds with an intercept,
    ``log(word_count)``, and drop-first prompt indicators.
    """
    if not (len(keys) == len(word_counts) == len(prompts) == len(labels_x2)):
        raise ValueError("baseline inputs must be equal-length sequences")
    if len(keys) < n_folds:
        raise ValueError("baseline needs at least one input per fold")
    word_count_arr = np.asarray(word_counts, dtype=float)
    label_arr = np.asarray(labels_x2, dtype=float)
    prompt_arr = np.asarray(prompts)
    unique_prompts = sorted(set(prompts))
    if len(unique_prompts) > 1:
        columns = [
            np.ones(len(keys)),
            np.log(np.maximum(word_count_arr, 1.0)),
        ]
        for prompt in unique_prompts[1:]:
            columns.append((prompt_arr == prompt).astype(float))
        design = np.column_stack(columns)
    else:
        design = np.column_stack(
            [np.ones(len(keys)), np.log(np.maximum(word_count_arr, 1.0))]
        )
    predictions = np.empty(len(keys), dtype=float)
    fold_bounds = np.array_split(np.arange(len(keys)), n_folds)
    for fold in fold_bounds:
        if fold.size == 0:
            continue
        train = np.setdiff1d(np.arange(len(keys)), fold, assume_unique=False)
        coefficients, *_ = np.linalg.lstsq(design[train], label_arr[train], rcond=None)
        predictions[fold] = design[fold] @ coefficients
    return predictions / 2.0


__all__ = ["baseline_predictions"]
