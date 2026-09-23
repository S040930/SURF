import json
from types import SimpleNamespace

from app.experiment.memory_study import V3_R2_PROTOCOL_ID
from app.experiment.memory_study.worker import _minimal_memory_case, _score_messages


def test_v3_r2_score_request_hides_condition_and_retrieval_metadata():
    record = {
        "protocol_id": V3_R2_PROTOCOL_ID,
        "question_text": "What is the answer?",
        "reference_answer": "The reference answer.",
        "student_answer": "A student answer.",
        "score_floor": 0.25,
        "score_ceiling": 1.0,
    }
    retrieved = SimpleNamespace(
        payload={
            "question": "What is the answer?",
            "answer": "Historical answer.",
            "manual_score": 0.75,
            "manual_feedback": "Historical feedback.",
        },
        answer_id="hidden-answer-id",
        similarity=0.99,
        native_relevance_score=0.98,
    )
    memory = [_minimal_memory_case(retrieved)]
    messages = _score_messages(
        SimpleNamespace(condition="mem0_full"), record, memory
    )
    payload = json.loads(messages[1]["content"])

    assert set(payload) == {
        "question",
        "reference_answer",
        "answer",
        "score_floor",
        "score_ceiling",
        "memory",
    }
    assert payload["memory"] == [
        {
            "student_answer": "Historical answer.",
            "teacher_score": 0.75,
            "teacher_feedback": "Historical feedback.",
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    for hidden in ("mem0_full", "hidden-answer-id", "similarity", "rank", "verification"):
        assert hidden not in serialized


def test_v3_r2_accepts_continuous_instrument_bounds_without_a_score_grid():
    record = {
        "protocol_id": V3_R2_PROTOCOL_ID,
        "question_text": "Question",
        "reference_answer": "Reference",
        "student_answer": "Answer",
        "score_floor": 0.25,
        "score_ceiling": 1.0,
    }
    messages = _score_messages(SimpleNamespace(condition="no_memory"), record, [])
    payload = json.loads(messages[1]["content"])
    assert payload["score_floor"] == 0.25
    assert payload["score_ceiling"] == 1.0
    assert "score_grid" not in payload
