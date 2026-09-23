from app.experiment.memory_study.analysis import compute_report
from app.experiment.memory_study.protocol import PreflightOutput, expected_call_counts
from app.experiment.memory_study.worker import slot_specs


def test_formal_v3_uses_four_luna_slots_and_expected_workload():
    slots = slot_specs()
    assert len(slots) == 4
    assert len({value for value in slots.values()}) == 4
    assert all(model == "gpt-6-luna" for model, _condition in slots.values())
    assert expected_call_counts("formal")["score_calls"] == 2_400
    assert expected_call_counts("formal")["memory_write_calls"] == 1_440


def test_slot_specs_accepts_the_frozen_model_list():
    slots = slot_specs(["gpt-6-luna"])
    assert list(slots) == [
        "slot-luna-no-memory",
        "slot-luna-retrieval-full",
        "slot-luna-mem0-full",
        "slot-luna-amem-full",
    ]


def test_preflight_accepts_legacy_probe_alias_but_emits_strict_probe_token():
    value = PreflightOutput.model_validate({"ok": True, "probe": "probe"})
    assert value.model_dump() == {"ok": True, "probe_token": "probe"}


def test_report_keeps_training_and_test_discrepancy_metrics_separate():
    rows = [
        {
            "model": "gpt-6-luna",
            "question_id": "q1",
            "condition": "no_memory",
            "feedback_mode": "full",
            "order_variant": "order_1",
            "answer_id": "train-1",
            "repeat": 0,
            "split": "train",
            "train_step": 1,
            "model_score": 3,
            "teacher_score": 4,
            "max_score": 5,
        },
        {
            "model": "gpt-6-luna",
            "question_id": "q1",
            "condition": "no_memory",
            "feedback_mode": "full",
            "order_variant": "order_1",
            "answer_id": "test-1",
            "repeat": 1,
            "split": "test",
            "model_score": 5,
            "teacher_score": 4,
            "max_score": 5,
        },
    ]
    report = compute_report(rows)
    metrics = report["split_metrics"]
    assert metrics["train"]["gpt-6-luna"]["no_memory"]["signed_bias"] == -1
    assert metrics["test"]["gpt-6-luna"]["no_memory"]["mae"] == 1
    assert report["analysis_method"]["bootstrap"] is False
