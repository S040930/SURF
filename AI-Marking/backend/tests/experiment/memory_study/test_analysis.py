import pytest

from app.experiment.memory_study import V3_R2_PROTOCOL_ID
from app.experiment.memory_study.analysis import compute_report

MODEL = "gpt-5.6-luna"


def _row(condition, score, *, question="q1", answer="a1", order="order_1", split="test", train_step=None):
    return {
        "model": MODEL, "question_id": question, "answer_id": answer,
        "condition": condition, "feedback_mode": "full", "order_variant": order,
        "repeat": 1, "model_score": score, "teacher_score": 1.0,
        "max_score": 1.0, "split": split, "train_step": train_step,
    }


def rows():
    values = []
    scores = {
        "no_memory": (0.6, 0.6, 0.6), "retrieval_full": (0.8, 0.9, 1.0),
        "mem0_full": (0.5, 0.6, 0.7), "amem_full": (0.7, 0.8, 0.9),
    }
    for condition, order_scores in scores.items():
        for index, score in enumerate(order_scores, start=1):
            values.append(_row(condition, score, order=f"order_{index}"))
    return values


def test_report_is_descriptive_and_primary_is_test_only():
    report = compute_report(rows())
    method = report["analysis_method"]
    assert method["bootstrap"] is False
    assert method["resampling"] is False
    assert method["significance_tests"] is False
    assert method["primary_scope"] == "test_only_new_answers"
    assert "test_all_conditions" not in report["primary"]
    assert report["sample"]["train_successful_scores"] == 0


def test_condition_nae_averages_orders_within_answer():
    condition = compute_report(rows())["primary"]["condition_nae"][MODEL]
    assert condition["no_memory"]["estimate"] == pytest.approx(0.4)
    assert condition["retrieval_full"]["estimate"] == pytest.approx(0.1)
    assert condition["mem0_full"]["estimate"] == pytest.approx(0.4)
    assert condition["amem_full"]["estimate"] == pytest.approx(0.2)
    assert condition["retrieval_full"]["n_answers"] == 1


def test_memory_gain_is_answer_paired_and_positive_means_improvement():
    gains = compute_report(rows())["primary"]["memory_gain_vs_no_memory"][MODEL]
    assert gains["retrieval_full"]["estimate"] == pytest.approx(0.3)
    assert gains["retrieval_full"]["improved"] == 1
    assert gains["mem0_full"]["estimate"] == pytest.approx(0.0)
    assert gains["mem0_full"]["tied"] == 1
    assert gains["amem_full"]["estimate"] == pytest.approx(0.2)


def test_missing_order_excludes_answer_from_complete_pair():
    incomplete = [row for row in rows() if not (row["condition"] == "retrieval_full" and row["order_variant"] == "order_3")]
    report = compute_report(incomplete)
    condition = report["primary"]["condition_nae"][MODEL]["retrieval_full"]
    assert condition["estimate"] is None
    assert condition["n_answers"] == 0
    assert condition["excluded_answers"] == 1
    gain = report["primary"]["memory_gain_vs_no_memory"][MODEL]["retrieval_full"]
    assert gain["n_answers"] == 0
    assert gain["excluded_answers"] == 1


def test_order_sensitivity_uses_order_means_not_individual_extremes():
    data = []
    for answer in ("easy", "hard"):
        for order, error in (("order_1", 0.1), ("order_2", 0.2), ("order_3", 0.3)):
            data.append(_row("retrieval_full", 1.0 - error, answer=answer, order=order))
            data.append(_row("no_memory", 0.6, answer=answer, order=order))
    value = compute_report(data)["secondary"]["history_order_sensitivity"][MODEL]["retrieval_full"]
    assert value["mean_range"] == pytest.approx(0.2)
    assert value["by_question"]["q1"]["n_answers"] == 2


def test_question_macro_equal_weights_questions():
    data = rows()
    for answer in ("b1", "b2", "b3"):
        for order in ("order_1", "order_2", "order_3"):
            data.append(_row("no_memory", 1.0, question="q2", answer=answer, order=order))
            data.append(_row("retrieval_full", 0.0, question="q2", answer=answer, order=order))
    estimate = compute_report(data)["primary"]["condition_nae"][MODEL]["retrieval_full"]["estimate"]
    assert estimate == pytest.approx((0.1 + 1.0) / 2)


def test_training_trajectory_is_separate_and_ordered_by_train_step():
    data = rows() + [
        _row("retrieval_full", 0.5, answer="train-2", split="train", train_step=2),
        _row("retrieval_full", 0.75, answer="train-1", split="train", train_step=1),
    ]
    report = compute_report(data)
    stream = report["diagnostics"]["training_memory_trajectory"][MODEL]["retrieval_full / q1 / order_1"]
    assert [row["answer_id"] for row in stream["records"]] == ["train-1", "train-2"]
    assert [row["memory_items_before"] for row in stream["records"]] == [0, 1]
    assert report["primary"]["condition_nae"][MODEL]["retrieval_full"]["n_answers"] == 1


def test_report_protocol_can_follow_the_persisted_study_protocol():
    assert compute_report(rows(), protocol_id="saf-memory-framework-v3")["protocol"] == "saf-memory-framework-v3"


def test_v3_r2_report_uses_the_v4_original_and_shuffled_estimands():
    data = []
    for condition, scores in {
        "no_memory": (0.6, 0.6),
        "retrieval_full": (0.8, 0.9),
        "mem0_full": (0.5, 0.6),
        "amem_full": (0.7, 0.8),
    }.items():
        for order, score in zip(("original", "shuffled"), scores):
            data.append(_row(condition, score, order=order))
    report = compute_report(data, protocol_id=V3_R2_PROTOCOL_ID)
    assert report["protocol"] == V3_R2_PROTOCOL_ID
    assert report["analysis_method"]["order_variants"] == [
        "original",
        "shuffled",
    ]
    assert report["analysis_method"]["order_sensitivity"] == (
        "v4_original_vs_shuffled_range_for_memory_conditions"
    )
    assert report["secondary"]["history_order_sensitivity"][MODEL][
        "retrieval_full"
    ]["orders"] == ["original", "shuffled"]
