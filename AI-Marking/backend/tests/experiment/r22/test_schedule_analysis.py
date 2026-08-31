from __future__ import annotations

from app.experiment.r22.analysis import compute_analysis, training_step_errors
from app.experiment.r22.executor import call_sequence, expected_total_calls
from app.experiment.r22.protocol import PILOT_SCHEDULE, expected_question_calls


def test_r22_schedule_is_40_training_10_test_and_expected_call_count():
    assert PILOT_SCHEDULE.memory_count == 40
    assert PILOT_SCHEDULE.test_count == 10
    assert PILOT_SCHEDULE.checkpoints == (10, 20, 30, 40)
    assert expected_question_calls() == 1320
    assert expected_total_calls() == 2640


def test_training_score_precedes_memory_update_and_uses_previous_history():
    specs = call_sequence(
        "q",
        "crm",
        1,
        [f"train-{index}" for index in range(40)],
        [f"test-{index}" for index in range(10)],
    )
    first = specs[:2]
    assert [(row.kind, row.history_count, row.position) for row in first] == [
        ("train_score", 0, 1),
        ("memory_update", 1, 1),
    ]
    step_10 = [row for row in specs if row.position == 10]
    assert step_10[0].kind == "train_score"
    assert step_10[0].history_count == 9
    assert step_10[1].kind == "memory_update"
    assert step_10[1].history_count == 10
    assert sum(row.kind == "train_score" for row in specs) == 40
    assert sum(row.kind == "memory_update" for row in specs) == 40
    assert sum(row.kind == "test_score" for row in specs) == 80


def test_nm_has_training_scores_but_no_memory_updates():
    specs = call_sequence(
        "q", "nm", 2, [f"train-{index}" for index in range(40)], [f"test-{index}" for index in range(10)]
    )
    assert sum(row.kind == "train_score" for row in specs) == 40
    assert sum(row.kind == "memory_update" for row in specs) == 0
    assert {row.history_count for row in specs if row.kind == "test_score"} == {10, 20, 30, 40}


def test_training_step_errors_are_raw_absolute_differences():
    rows = [
        {
            "question_id": "q",
            "condition": "crm",
            "trajectory": 1,
            "position": 1,
            "answer_id": "a1",
            "model_score": 2.25,
            "teacher_score": 1.0,
            "max_score": 3.5,
        },
        {
            "question_id": "q",
            "condition": "crm",
            "trajectory": 1,
            "position": 2,
            "answer_id": "a2",
            "model_score": 1.5,
            "teacher_score": 2.0,
            "max_score": 3.5,
        },
    ]
    errors = training_step_errors(rows)
    assert [row["absolute_error"] for row in errors] == [1.25, 0.5]
    report = compute_analysis(rows, [])
    assert [row["step"] for row in report["training_mae_by_step"]] == [1, 2]
    assert report["training_mae_by_condition"][0]["mae"] == 0.875
