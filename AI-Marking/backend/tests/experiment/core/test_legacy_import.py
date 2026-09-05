"""Tests for the read-only legacy r23 importer."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.experiment.core.legacy_import import SOURCE_SYSTEM, import_legacy_r23
from app.models.experiments import (
    ExpCallAttempt,
    ExpCallScore,
    ExpDataset,
    ExpDatasetRevision,
    ExpEvent,
    ExpEvaluationSlot,
    ExpInput,
    ExpInputLabel,
    ExpLockedReport,
    ExpObservationSlot,
    ExpProject,
    ExpProjectRunner,
    ExpRubricVersion,
    ExpRunnerConfig,
    ExpUniqueEvaluation,
)
from app.models.r23 import (
    R23CallAttempt,
    R23EvaluationObservation,
    R23Event,
    R23LockedReport,
    R23ModelBinding,
    R23Project,
    R23RubricVersion,
    R23RunnerConfig,
    R23SampleObservation,
    R23UniqueEvaluation,
)
from app.services.experiments import canonical_sha256


def _make_r23_fixture(db_session) -> str:
    runner = R23RunnerConfig(
        id=str(uuid4()),
        name="legacy runner",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        speed_mode="standard",
        timeout_seconds=120,
        config_sha256="r" * 64,
        frozen_runtime_json={"service_tier": "default", "cli_version": "codex-fake 1"},
        status="ready",
    )
    rubric = R23RubricVersion(
        id=str(uuid4()),
        protocol_id="r23-test",
        name="legacy rubric",
        rubric_text="Content, organization, language rubric text.",
        rubric_sha256="h" * 64,
        status="ready",
    )
    project = R23Project(
        id=str(uuid4()),
        protocol_id="r23-test",
        name="legacy formal",
        kind="formal",
        status="completed",
        rubric_version_id=rubric.id,
        data_processing_confirmed=True,
        data_status_json={"ready": True, "datasets": []},
        manifest_json={"observations": []},
        manifest_sha256="m" * 64,
        results_embargoed=False,
        report_sha256="p" * 64,
    )
    db_session.add_all([runner, rubric, project])
    db_session.flush()
    binding = R23ModelBinding(
        id=str(uuid4()),
        project_id=project.id,
        runner_config_id=runner.id,
        position=1,
        model="gpt-5.6-luna",
        config_sha256="r" * 64,
        frozen_runtime_json={"service_tier": "default"},
    )
    db_session.add(binding)
    db_session.flush()

    observation_ids = []
    for index, (dimension, label_x2) in enumerate(
        [("content", 4), ("content", 4), ("organization", 8)]
    ):
        observation = R23SampleObservation(
            project_id=project.id,
            observation_key=f"slot-{index}",
            dimension=dimension,
            source_id_hash=f"s{index}" * 8,
            label_x2=label_x2,
            prompt_sha256=f"p{index}" * 8,
            input_sha256=("shared" if index < 2 else f"solo{index}") + "0" * 54,
            word_count=90 + index,
            collision=index == 1,
            rerun=index == 2,
        )
        db_session.add(observation)
        observation_ids.append(observation)
    db_session.flush()

    evaluation_ids = {}
    for run_index in (0, 1):
        evaluation = R23UniqueEvaluation(
            project_id=project.id,
            model_binding_id=binding.id,
            input_sha256="shared" + "0" * 54,
            run_index=run_index,
            status="succeeded",
            prompt_text="prompt text",
            essay_text="essay text",
            content_score_x2=4,
            organization_score_x2=8,
            language_score_x2=6,
            attempt_count=1,
            latency_ms=1000,
        )
        db_session.add(evaluation)
        db_session.flush()
        evaluation_ids[run_index] = evaluation.id
    db_session.add_all(
        [
            R23EvaluationObservation(
                evaluation_id=evaluation_ids[0], observation_id=observation_ids[0].id
            ),
            R23EvaluationObservation(
                evaluation_id=evaluation_ids[0], observation_id=observation_ids[1].id
            ),
            R23EvaluationObservation(
                evaluation_id=evaluation_ids[0], observation_id=observation_ids[2].id
            ),
            R23EvaluationObservation(
                evaluation_id=evaluation_ids[1], observation_id=observation_ids[2].id
            ),
        ]
    )
    db_session.add(
        R23CallAttempt(
            project_id=project.id,
            evaluation_id=evaluation_ids[0],
            attempt_number=1,
            input_sha256="shared" + "0" * 54,
            requested_model="gpt-5.6-luna",
            reasoning_effort="medium",
            speed_mode="standard",
            timeout_seconds=120,
            status="succeeded",
            latency_ms=1000,
            output_sha256="o" * 64,
            started_at=project.created_at,
        )
    )
    db_session.add(
        R23Event(project_id=project.id, event_type="project_created", detail_json={})
    )
    db_session.add(
        R23LockedReport(
            project_id=project.id,
            report_json={"protocol_id": "r23-test", "cells": []},
            report_sha256=canonical_sha256({"protocol_id": "r23-test", "cells": []}),
            figure_svg="<svg>legacy</svg>",
            figure_sha256="f" * 64,
        )
    )
    db_session.commit()
    return project.id


def _make_revision(db_session) -> str:
    dataset = ExpDataset(
        id=str(uuid4()),
        key="dress_case",
        name="legacy test dataset",
        access_level="restricted",
    )
    revision = ExpDatasetRevision(
        id=str(uuid4()),
        dataset_id=dataset.id,
        revision_label="test",
        file_specs_json=[],
        audit_json={},
        status="verified",
    )
    db_session.add_all([dataset, revision])
    db_session.commit()
    return revision.id


def _r23_counts(db_session) -> dict[str, int]:
    return {
        model.__tablename__: db_session.scalar(
            select(func.count()).select_from(model)
        )
        for model in (
            R23Project,
            R23RunnerConfig,
            R23RubricVersion,
            R23ModelBinding,
            R23SampleObservation,
            R23UniqueEvaluation,
            R23EvaluationObservation,
            R23CallAttempt,
            R23Event,
            R23LockedReport,
        )
    }


def test_import_is_read_only_verbatim_and_idempotent(db_session):
    source_id = _make_r23_fixture(db_session)
    revision_id = _make_revision(db_session)
    before = _r23_counts(db_session)

    summary = import_legacy_r23(db_session, dataset_revision_id=revision_id)

    assert summary["imported"] == [source_id]
    assert summary["r23_tables_written"] == 0
    assert _r23_counts(db_session) == before

    project = db_session.get(ExpProject, source_id)
    assert project.read_only is True
    assert project.source_system == SOURCE_SYSTEM
    assert project.status == "completed"
    assert project.results_embargoed is False
    assert project.import_verification_json["report_sha256_match"] is True
    assert project.import_verification_json["source_counts"]["evaluations"] == 2

    inputs = db_session.scalars(select(ExpInput)).all()
    assert len(inputs) == 2  # shared + solo2
    slots = db_session.scalars(select(ExpObservationSlot)).all()
    assert len(slots) == 3
    evaluations = db_session.scalars(select(ExpUniqueEvaluation)).all()
    assert len(evaluations) == 2
    joins = db_session.scalars(select(ExpEvaluationSlot)).all()
    # Slot 0 and 1 map to the primary evaluation; slot 2 maps to both runs.
    assert len(joins) == 4
    assert db_session.scalar(select(func.count()).select_from(ExpCallScore)) == 6
    assert db_session.scalar(select(func.count()).select_from(ExpCallAttempt)) == 1
    assert db_session.scalar(select(func.count()).select_from(ExpEvent)) == 1
    labels = db_session.scalars(select(ExpInputLabel)).all()
    assert len(labels) == 2  # first label per (input, channel)

    report = db_session.get(ExpLockedReport, source_id)
    assert report.report_json == {"protocol_id": "r23-test", "cells": []}
    assert report.figures_json[0]["svg"] == "<svg>legacy</svg>"
    assert report.report_sha256 == canonical_sha256(report.report_json)

    # Mutations are refused on read-only imports.
    from app.services.experiments import ExperimentDomainError, ExperimentService

    with pytest.raises(ExperimentDomainError):
        ExperimentService(db_session).start_project(source_id)

    # Re-running skips existing projects and touches nothing.
    again = import_legacy_r23(db_session, dataset_revision_id=revision_id)
    assert again["imported"] == []
    assert again["skipped_existing"] == [source_id]
    assert _r23_counts(db_session) == before


def test_import_refuses_a_running_legacy_project(db_session):
    source_id = _make_r23_fixture(db_session)
    revision_id = _make_revision(db_session)
    project = db_session.get(R23Project, source_id)
    project.status = "running"
    db_session.commit()
    with pytest.raises(ValueError, match="still running"):
        import_legacy_r23(db_session, dataset_revision_id=revision_id)
