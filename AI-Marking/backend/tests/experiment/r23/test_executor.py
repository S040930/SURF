from uuid import uuid4

from app.experiment.r21.codex_runner import CodexResult, RunnerExecutionError
from app.experiment.r23.executor import run_one
from app.models.r23 import (
    R23CallAttempt,
    R23ModelBinding,
    R23Project,
    R23RubricVersion,
    R23RunGroup,
    R23UniqueEvaluation,
)


class InvalidScoreRunner:
    def assert_matches(self, _runtime):
        return None

    def run(self, **_kwargs):
        return CodexResult(
            value={"content": 3.25, "organization": 3.5, "language": 4.0},
            latency_ms=25,
            exit_code=0,
            stderr_excerpt="",
        )


class InvalidSchemaRunner:
    def assert_matches(self, _runtime):
        return None

    def run(self, **_kwargs):
        raise RunnerExecutionError(
            "codex structured result failed validation: student essay text"
        )


def _pending_evaluation(db_session):
    rubric = R23RubricVersion(
        id=str(uuid4()),
        name="rubric",
        rubric_text="Content, Organization, and Language are each scored from 1 to 5. "
        * 2,
        rubric_sha256="r" * 64,
        status="frozen",
    )
    project = R23Project(
        id=str(uuid4()),
        protocol_id="r23-test",
        name=f"validation-{uuid4()}",
        kind="pilot_run",
        status="running",
        rubric_version_id=rubric.id,
        data_processing_confirmed=True,
        data_status_json={},
        manifest_json={},
    )
    binding = R23ModelBinding(
        id=str(uuid4()),
        project_id=project.id,
        runner_config_id="runner",
        position=1,
        model="model-a",
        config_sha256="c" * 64,
        frozen_runtime_json={
            "reasoning_effort": "medium",
            "speed_mode": "standard",
            "timeout_seconds": 120,
        },
    )
    group = R23RunGroup(
        project_id=project.id,
        model_binding_id=binding.id,
        dimension="content",
        run_index=0,
        status="pending",
        order_rank=1,
        expected_calls=1,
        completed_calls=0,
    )
    db_session.add_all([rubric, project, binding, group])
    db_session.flush()
    evaluation = R23UniqueEvaluation(
        project_id=project.id,
        model_binding_id=binding.id,
        run_group_id=group.id,
        input_sha256="i" * 64,
        run_index=0,
        status="pending",
        prompt_text="prompt",
        essay_text="student essay text",
    )
    db_session.add(evaluation)
    db_session.commit()
    return project, group, evaluation


def test_invalid_score_value_requires_manual_retry_without_output_leak(db_session):
    project, group, evaluation = _pending_evaluation(db_session)

    assert run_one(db_session, worker_id="test", runner_factory=InvalidScoreRunner)

    assert project.status == "attention_required"
    assert group.status == "blocked"
    assert evaluation.status == "attention_required"
    assert evaluation.attempt_count == 1
    assert evaluation.input_sha256 == "i" * 64
    assert evaluation.failure_code == "invalid_score_value"
    assert evaluation.failure_summary == "模型返回的分数必须为 1–5，且间隔为 0.5"
    attempt = db_session.query(R23CallAttempt).one()
    assert attempt.error_code == "invalid_score_value"
    assert "student essay text" not in attempt.error_summary


def test_invalid_score_schema_requires_manual_retry_without_output_leak(db_session):
    project, group, evaluation = _pending_evaluation(db_session)

    assert run_one(db_session, worker_id="test", runner_factory=InvalidSchemaRunner)

    assert project.status == "attention_required"
    assert group.status == "blocked"
    assert evaluation.status == "attention_required"
    assert evaluation.attempt_count == 1
    assert evaluation.failure_code == "invalid_score_schema"
    assert (
        evaluation.failure_summary
        == "模型未返回仅含 content、organization、language 三字段的有效 JSON 分数对象"
    )
    attempt = db_session.query(R23CallAttempt).one()
    assert attempt.error_code == "invalid_score_schema"
    assert "student essay text" not in attempt.error_summary
