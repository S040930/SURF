from __future__ import annotations

import pytest

from app.experiment.r20.analysis import compute_analysis


def _rows():
    rows = []
    for model in ("m1", "m2"):
        for question in ("q1", "q2"):
            for entry, group, truth in (("a", "g1", 1.0), ("b", "g2", 0.0)):
                for trajectory in (1, 2, 3):
                    for condition, score in (
                        ("nm", 0.5),
                        ("crm", 0.25),
                        ("arm", truth),
                    ):
                        for repeat in (1, 2):
                            rows.append(
                                {
                                    "model_id": model,
                                    "question_id": question,
                                    "group_id": group,
                                    "entry_id": entry,
                                    "condition": condition,
                                    "trajectory": trajectory,
                                    "history_count": 40,
                                    "repeat": repeat,
                                    "model_score": score,
                                    "teacher_score": truth,
                                    "max_score": 1.0,
                                }
                            )
    return rows


def test_analysis_is_question_macro_crossed_and_has_loo():
    result = compute_analysis(_rows(), bootstrap_reps=100, seed="fixed")
    core = result["core"]
    assert core["primary_arm_minus_crm"] < 0
    assert core["bootstrap_95"]["reps"] == 100
    assert set(core["by_model"]) == {"m1", "m2"}
    assert set(core["leave_one_question_out"]) == {"q1", "q2"}
    assert set(core["trajectory_arm_minus_crm"]) == {"1", "2", "3"}


def test_analysis_rejects_missing_final_repeat():
    rows = _rows()
    rows.pop()
    with pytest.raises(ValueError, match="exactly 2 repeats"):
        compute_analysis(rows, bootstrap_reps=10, seed="fixed")
