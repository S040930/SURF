from app.experiment.memory_study import (
    DEFAULT_PROTOCOL_ID,
    PROTOCOL_ID,
    V3_R2_PROTOCOL_ID,
)
from app.experiment.memory_study.protocol import (
    CONDITIONS,
    FRAMEWORK_CONDITIONS,
    LEGACY_ORDER_VARIANTS,
    MODEL_IDS,
    ORDER_SHUFFLE_SEED,
    ORDER_VARIANTS,
    V3_R2_FORMAL_QUESTIONS,
    expected_call_counts,
    scoring_context_for_protocol,
    retrieval_top_k_for_protocol,
)


def test_protocol_is_independent_and_has_no_legacy_controls():
    assert PROTOCOL_ID == "saf-memory-framework-v3"
    assert V3_R2_PROTOCOL_ID == "saf-memory-framework-v3-r2"
    assert DEFAULT_PROTOCOL_ID == V3_R2_PROTOCOL_ID
    assert len(V3_R2_FORMAL_QUESTIONS) == 6
    assert MODEL_IDS == ("gpt-6-luna",)
    assert ORDER_VARIANTS == ("original", "shuffled")
    assert ORDER_SHUFFLE_SEED == 42
    assert LEGACY_ORDER_VARIANTS == ("order_1", "order_2", "order_3")
    assert len(CONDITIONS) == 7
    assert len(FRAMEWORK_CONDITIONS) == 6
    counts = expected_call_counts("formal")
    assert counts["score_calls"] == 2_400
    assert counts["memory_write_calls"] == 1_440
    assert counts["total_score_results"] == 2_400
    with_no_feedback = expected_call_counts("formal", ["full", "no_feedback"])
    assert with_no_feedback["score_calls"] == 4_200
    assert with_no_feedback["memory_write_calls"] == 2_880

    legacy = expected_call_counts("formal", protocol_id=PROTOCOL_ID)
    assert legacy["order_variant_count"] == 3
    assert legacy["score_calls"] == 3_600
    assert legacy["memory_write_calls"] == 2_160


def test_v3_r2_keeps_v3_queue_and_has_an_independent_scoring_instrument():
    assert expected_call_counts(
        "formal", ["full", "no_feedback"], protocol_id=V3_R2_PROTOCOL_ID
    ) == expected_call_counts("formal", ["full", "no_feedback"])
    context = scoring_context_for_protocol(V3_R2_PROTOCOL_ID)
    assert context["protocol_id"] == V3_R2_PROTOCOL_ID
    assert context["instrument_sha256"]
    assert "condition" not in context["payload_fields"]
    assert "score_floor" in context["payload_fields"]
    assert "score_ceiling" in context["payload_fields"]
    assert retrieval_top_k_for_protocol(PROTOCOL_ID) == 20
    assert retrieval_top_k_for_protocol(V3_R2_PROTOCOL_ID) == 5


def test_development_and_pilot_use_one_order_and_one_repeat():
    for kind in ("development", "pilot"):
        counts = expected_call_counts(kind)
        assert counts["score_calls"] == 70
        assert counts["memory_write_calls"] == 160
        assert counts["order_variant_count"] == 1
        assert counts["repeat_count"] == 1
