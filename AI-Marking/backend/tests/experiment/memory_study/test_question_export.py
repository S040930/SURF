"""Interim per-question export: analysis slice, HTML rendering, CLI payload."""

import json

import pytest
from sqlalchemy import select

from app.core.time import utc_now_naive
from app.experiment.memory_study.analysis import compute_question_report
from app.experiment.memory_study.report_html import (
    render_question_report_html,
    render_question_report_json,
)
from app.models.memory_study import MSQuestionRun, MSRecord, MSStudy
from scripts.export_memory_study_question import (
    ExportError,
    build_question_payload,
    render_csv,
)


def _make_study(db_session, *, question_id="q1"):
    study = MSStudy(
        id="study-export",
        protocol_id="p",
        name="formal-six",
        kind="formal",
        status="running",
        data_processing_confirmed=True,
        data_manifest_json={
            "orders": {question_id: {"order_1": ["a1"]}},
            "score_floors": {question_id: 0.0},
        },
        config_json={},
        expected_json={},
        progress_json={},
        integrity_json={},
        results_embargoed=True,
    )
    db_session.add(study)
    db_session.add(
        MSQuestionRun(study_id=study.id, question_id=question_id, status="completed")
    )
    db_session.add(
        MSRecord(
            study_id=study.id,
            answer_id="a1",
            group_id="g1",
            question_id=question_id,
            question_text="t",
            reference_answer="r",
            student_answer="s",
            teacher_score=8,
            teacher_feedback="f",
            verification_feedback="v",
            source_split="test",
            selection_kind="test",
            input_sha256="hash",
            source_path="p",
            source_position=0,
        )
    )
    db_session.commit()
    return study


def _add_score(
    db_session,
    study_id,
    *,
    question_id="q1",
    answer_id="a1",
    model="gpt-5.6-luna",
    condition="no_memory",
    repeat=1,
    status="succeeded",
    model_score=7,
    manual_score=8,
    max_score=10,
    **kw,
):
    from app.models.memory_study import MSCall

    call = MSCall(
        study_id=study_id,
        model=model,
        question_id=question_id,
        condition=condition,
        framework="none",
        feedback_mode="full",
        order_variant="order_1",
        kind="score",
        answer_id=answer_id,
        repeat=repeat,
        status=status,
        manual_score=manual_score,
        model_score=model_score,
        max_score=max_score,
        signed_diff=model_score - manual_score,
        absolute_diff=abs(model_score - manual_score),
        normalized_absolute_diff=abs(model_score - manual_score) / max_score,
        **kw,
    )
    db_session.add(call)
    db_session.commit()
    return call


def _row(**overrides):
    row = {
        "model": "gpt-5.6-luna",
        "question_id": "q1",
        "answer_id": "a1",
        "condition": "no_memory",
        "feedback_mode": "full",
        "order_variant": "order_1",
        "repeat": 1,
        "model_score": 7,
        "teacher_score": 8,
        "max_score": 10,
        "split": "test",
    }
    row.update(overrides)
    return row


def test_compute_question_report_single_slice_metrics():
    report = compute_question_report(
        [_row(), _row(condition="retrieval_full", model_score=9)]
    )
    assert report["report_type"] == "question_slice_descriptive"
    assert report["sample"]["question_id"] == "q1"
    assert report["sample"]["successful_scores"] == 2
    assert report["analysis_method"]["question_weighting"].startswith(
        "single_question_slice"
    )
    condition = report["primary"]["condition_nae"]["gpt-5.6-luna"]
    assert condition["no_memory"]["estimate"] == pytest.approx(0.1)
    assert condition["retrieval_full"]["estimate"] == pytest.approx(0.1)
    assert report["analysis_method"]["primary_scope"] == "test_only_new_answers"
    assert "repeat_stability" not in report["secondary"]
    assert report["grid_context"]["q1"]["denominator"] == 10
    assert report["grid_context"]["q1"]["training_observed_max"] is None
    assert report["report_sha256"]


def test_compute_question_report_rejects_multiple_questions_and_empty():
    with pytest.raises(ValueError):
        compute_question_report([_row(), _row(question_id="q2")])
    with pytest.raises(ValueError):
        compute_question_report([])


def test_render_html_contains_banner_and_detail():
    report = compute_question_report([_row()])
    html = render_question_report_html(
        report,
        [{"call_id": 1, "model": "gpt-5.6-luna", "condition": "no_memory"}],
        study_name="formal-six",
        question_id="q1",
        generated_at="2026-01-01 00:00:00 UTC",
    )
    assert "中期草稿" in html
    assert "formal-six" in html and "q1" in html
    assert "report_sha256" in html
    assert "逐条评分明细" in html
    assert "评分网格上下文（grid_context）" in html
    assert "复评稳定性" not in html
    assert "<table>" in html


def test_render_json_is_machine_readable():
    report = compute_question_report([_row()])
    payload = json.loads(
        render_question_report_json(report, [{"call_id": 1, "model_score": 7}])
    )
    assert payload["report"]["report_type"] == "question_slice_descriptive"
    assert payload["detail_rows"][0]["model_score"] == 7


def test_build_question_payload_requires_completed_shard(db_session):
    study = _make_study(db_session)
    db_session.add(MSQuestionRun(study_id=study.id, question_id="q2", status="running"))
    db_session.commit()
    with pytest.raises(ExportError, match="仅 completed"):
        build_question_payload(db_session, study.id, "q2")
    with pytest.raises(ExportError, match="不属于该研究的分片"):
        build_question_payload(db_session, study.id, "q9")
    with pytest.raises(ExportError, match="不存在"):
        build_question_payload(db_session, "no-such-study", "q1")


def test_build_question_payload_no_successful_scores(db_session):
    study = _make_study(db_session)
    with pytest.raises(ExportError, match="没有成功的评分调用"):
        build_question_payload(db_session, study.id, "q1")


def test_build_question_payload_collects_scores_and_failures(db_session):
    study = _make_study(db_session)
    _add_score(db_session, study.id, model_score=7)
    _add_score(db_session, study.id, condition="mem0_full", model_score=9)
    failed = _add_score(
        db_session,
        study.id,
        condition="amem_full",
        status="failed",
        failure_code="schema",
        failure_summary="bad output",
    )
    payload = build_question_payload(db_session, study.id, "q1")
    assert payload["score_call_count"] == 2
    assert payload["failed_call_count"] == 1
    assert payload["report"]["sample"]["successful_scores"] == 2
    assert payload["report"]["failures"][0]["call_id"] == failed.id
    assert payload["detail_rows"][0]["split"] == "test"
    assert payload["detail_rows"][0]["teacher_score"] == 8
    assert payload["report"]["sample"]["question_id"] == "q1"


def test_build_question_payload_derives_train_split(db_session):
    study = _make_study(db_session)
    record = db_session.scalar(
        select(MSRecord).where(
            MSRecord.study_id == study.id, MSRecord.answer_id == "a1"
        )
    )
    record.selection_kind = "training"
    db_session.commit()
    _add_score(db_session, study.id)
    payload = build_question_payload(db_session, study.id, "q1")
    assert payload["detail_rows"][0]["split"] == "train"
    assert payload["detail_rows"][0]["train_step"] == 1
    assert payload["detail_rows"][0]["memory_items_before"] == 0


def test_render_csv_matches_detail_rows(db_session):
    study = _make_study(db_session)
    _add_score(db_session, study.id, model_score=7, manual_score=8)
    payload = build_question_payload(db_session, study.id, "q1")
    csv_text = render_csv(payload["detail_rows"])
    lines = csv_text.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("call_id,model,condition")
    assert lines[0].endswith(",latency_ms")
    # teacher/model score columns carry the actual values
    assert ",8.0,7.0," in lines[1] or ",8,7," in lines[1]


def test_completed_shard_marked_by_worker_timestamps(db_session):
    study = _make_study(db_session)
    shard = db_session.scalar(
        select(MSQuestionRun).where(MSQuestionRun.study_id == study.id)
    )
    assert shard.completed_at is None
    shard.completed_at = utc_now_naive()
    db_session.commit()
    assert shard.status == "completed"
