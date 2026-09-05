"""Hand-computed fixture tests for the analysis toolkit."""

import numpy as np
import pytest

from app.experiment.core.analysis import (
    baseline_predictions,
    calibration_curve,
    exact_agreement_rate,
    paired_bootstrap_diff,
    quadratic_weighted_kappa,
    spearman_correlation,
    transition_matrix,
    weighted_mae,
    within_agreement_rate,
)
from app.experiment.core.analysis.metrics import MetricError

# Grid x2 in 2..4 (scores 1, 1.5, 2) for hand-checked cases.
MIN_X2, MAX_X2 = 2, 4


def test_qwk_hand_computed_cases():
    # Anti-correlated on a 3-category grid: QWK = 0.
    assert quadratic_weighted_kappa(
        [2, 2, 4, 4], [2, 4, 2, 4], min_x2=MIN_X2, max_x2=MAX_X2
    ) == pytest.approx(0.0)
    # One off-diagonal observation out of four: QWK = 0.5.
    assert quadratic_weighted_kappa(
        [2, 2, 2, 4], [2, 2, 4, 4], min_x2=MIN_X2, max_x2=MAX_X2
    ) == pytest.approx(0.5)
    # Perfect agreement: QWK = 1.
    assert quadratic_weighted_kappa(
        [2, 3, 4, 4], [2, 3, 4, 4], min_x2=MIN_X2, max_x2=MAX_X2
    ) == pytest.approx(1.0)


def test_weighted_qwk_hand_computed():
    # labels [2,2,2,4], scores [2,2,4,4], weights [2,1,1,4] → QWK = 0.75.
    assert quadratic_weighted_kappa(
        [2, 2, 2, 4],
        [2, 2, 4, 4],
        min_x2=MIN_X2,
        max_x2=MAX_X2,
        weights=[2, 1, 1, 4],
    ) == pytest.approx(0.75)


def test_weighted_mae_and_agreement_rates():
    labels = [2, 2, 4, 4]
    scores = [2, 4, 2, 4]
    weights = [1, 2, 3, 4]
    assert weighted_mae(labels, scores, weights=weights) == pytest.approx(0.5)
    assert exact_agreement_rate(labels, scores, weights=weights) == pytest.approx(0.5)
    assert within_agreement_rate(
        labels, scores, tolerance_x2=1, weights=weights
    ) == pytest.approx(0.5)
    assert within_agreement_rate(
        labels, scores, tolerance_x2=2, weights=weights
    ) == pytest.approx(1.0)
    assert weighted_mae(labels, scores) == pytest.approx(0.5)


def test_spearman_is_unweighted_sample_diagnostic():
    assert spearman_correlation([2, 2, 4, 4], [2, 4, 2, 4]) == pytest.approx(0.0)
    assert spearman_correlation([2, 3, 4, 5], [4, 5, 6, 7]) == pytest.approx(1.0)


def test_calibration_curve_bins_and_skips_empty_scores():
    points = calibration_curve(
        [2, 2, 4, 4],
        [2, 2, 4, 4],
        min_x2=MIN_X2,
        max_x2=MAX_X2,
        weights=[1, 1, 2, 2],
    )
    assert [(point["score_x2"], point["mean_label"], point["count"]) for point in points] == [
        (2, 2.0, 2),
        (4, 4.0, 2),
    ]
    assert points[0]["weight"] == 2.0
    assert points[1]["weight"] == 4.0


def test_transition_matrix_counts_label_prediction_pairs():
    matrix = transition_matrix(
        [2, 2, 4, 4], [2, 4, 2, 4], min_x2=MIN_X2, max_x2=MAX_X2
    )
    assert matrix == [
        [1, 0, 1],
        [0, 0, 0],
        [1, 0, 1],
    ]


def test_off_grid_inputs_are_rejected():
    with pytest.raises(MetricError):
        quadratic_weighted_kappa([1, 2], [2, 2], min_x2=MIN_X2, max_x2=MAX_X2)
    with pytest.raises(MetricError):
        transition_matrix([2, 11], [2, 2], min_x2=MIN_X2, max_x2=MAX_X2)
    with pytest.raises(MetricError):
        weighted_mae([], [])


def test_paired_bootstrap_diff_is_deterministic_and_zero_for_identical_models():
    labels = [2, 3, 4, 3, 2, 4]
    result = paired_bootstrap_diff(
        left=lambda labels, scores, weights: weighted_mae(labels, scores, weights=weights),
        right=lambda labels, scores, weights: weighted_mae(labels, scores, weights=weights),
        left_labels=labels,
        left_scores=[2, 3, 4, 3, 2, 4],
        right_labels=labels,
        right_scores=[2, 3, 4, 3, 2, 4],
        weights=[1.0] * 6,
        replicates=50,
        seed="seed-a",
    )
    assert result["observed_diff"] == pytest.approx(0.0)
    assert result["ci_low"] == pytest.approx(0.0)
    assert result["ci_high"] == pytest.approx(0.0)
    assert result["replicates"] == 50


def test_paired_bootstrap_diff_detects_a_real_gap():
    def mae_left(labels, scores, weights):
        return weighted_mae(labels, scores, weights=weights)

    labels = [4] * 20
    result = paired_bootstrap_diff(
        left=mae_left,
        right=mae_left,
        left_labels=labels,
        left_scores=[4] * 20,
        right_labels=labels,
        right_scores=[2] * 20,
        weights=[1.0] * 20,
        replicates=100,
        seed="seed-b",
    )
    assert result["observed_diff"] == pytest.approx(-1.0)
    assert result["ci_high"] < 0


def test_baseline_reproduces_an_exact_log_linear_relationship():
    word_counts = [100 * (1.2 ** index) for index in range(20)]
    labels_x2 = [3 + 2 * float(np.log(count)) for count in word_counts]
    keys = [f"key-{index}" for index in range(20)]
    prompts = ["same prompt"] * 20
    predictions = baseline_predictions(
        keys=keys,
        word_counts=word_counts,
        prompts=prompts,
        labels_x2=labels_x2,
        seed="seed-c",
        n_folds=10,
    )
    assert np.allclose(predictions, np.asarray(labels_x2) / 2.0)


def test_baseline_is_deterministic_and_honors_prompt_indicators():
    keys = [f"key-{index}" for index in range(12)]
    word_counts = [100 + 10 * index for index in range(12)]
    prompts = ["a" if index % 2 == 0 else "b" for index in range(12)]
    # Prompt a scores 2 points (x2) higher than prompt b at equal length.
    labels_x2 = [4 + (2 if prompt == "a" else 0) for prompt in prompts]
    first = baseline_predictions(
        keys=keys, word_counts=word_counts, prompts=prompts, labels_x2=labels_x2, seed="s"
    )
    second = baseline_predictions(
        keys=keys, word_counts=word_counts, prompts=prompts, labels_x2=labels_x2, seed="s"
    )
    assert np.array_equal(first, second)
    assert float(np.max(np.abs(first - np.asarray(labels_x2) / 2.0))) < 1e-9
