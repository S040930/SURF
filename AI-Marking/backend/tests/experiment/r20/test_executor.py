from app.experiment.r20.executor import expected_question_calls


def test_dynamic_call_formula():
    assert expected_question_calls(60, 15, 2) == 1440
    assert expected_question_calls(20, 5, 1) == 510
    assert 8 * expected_question_calls(60, 15, 2) == 11520
    assert 2 * expected_question_calls(20, 5, 1) == 1020
