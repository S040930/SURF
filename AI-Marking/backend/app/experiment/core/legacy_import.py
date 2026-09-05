"""One-off, re-runnable read-only import of the legacy r23 stack.

Copies r23 projects, run snapshots, call audits, and locked reports into the
unified ``exp_`` tables as read-only ``legacy-r23`` projects.  Never re-scores,
never re-analyses, never rewrites a report, and never writes to any ``r23_*``
table; per-project verification counts are stored on the imported project.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experiment.templates.dress_case import TEMPLATE_ID as CASE_TEMPLATE_ID
from app.models.experiments import (
    ExpCallAttempt,
    ExpCallScore,
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
from app.services.experiments import ExperimentService, canonical_sha256

SOURCE_SYSTEM = "legacy-r23"


def _verification(
    *,
    source: R23Project,
    counts: dict[str, int],
    report_sha256: str | None,
    report_match: bool,
) -> dict[str, Any]:
    return {
        "source_project_id": source.id,
        "source_status": source.status,
        "source_counts": counts,
        "report_sha256_expected": report_sha256,
        "report_sha256_match": report_match,
        "r23_tables_written": 0,
    }


def import_legacy_r23(
    db: Session,
    *,
    datasets_root: Path | None = None,
    dataset_revision_id: str | None = None,
) -> dict[str, Any]:
    """Import every r23 project; idempotent per source project."""
    if dataset_revision_id is None:
        service = ExperimentService(db)
        if datasets_root is not None:
            service.datasets_root = datasets_root
        _, revision, _ = service.ensure_dataset_revision("dress_case")
        dataset_revision_id = revision.id
    else:
        revision_row = db.get(ExpDatasetRevision, dataset_revision_id)
        if revision_row is None:
            raise ValueError(f"unknown dataset revision: {dataset_revision_id}")

    imported: list[str] = []
    skipped: list[str] = []
    projects = db.scalars(select(R23Project).order_by(R23Project.created_at)).all()
    for source in projects:
        existing = db.scalar(
            select(ExpProject).where(
                ExpProject.source_system == SOURCE_SYSTEM,
                ExpProject.source_project_id == source.id,
            )
        )
        if existing is not None:
            skipped.append(source.id)
            continue
        _import_project(db, source, dataset_revision_id)
        imported.append(source.id)
    db.commit()
    return {
        "imported": imported,
        "skipped_existing": skipped,
        "dataset_revision_id": dataset_revision_id,
        "r23_tables_written": 0,
    }


def _import_project(db: Session, source: R23Project, dataset_revision_id: str) -> None:
    if source.status == "running":
        # A running legacy project cannot be faithfully frozen post hoc; the
        # operator must terminate or complete it in the old stack first.
        raise ValueError(
            f"r23 project {source.id} is still running; resolve it before import"
        )

    # --- runners and rubric (get-or-create by id) ---
    bindings = db.scalars(
        select(R23ModelBinding).where(R23ModelBinding.project_id == source.id)
    ).all()
    for binding in bindings:
        if db.get(ExpRunnerConfig, binding.runner_config_id) is None:
            runner = db.get(R23RunnerConfig, binding.runner_config_id)
            if runner is None:
                raise ValueError(f"missing r23 runner {binding.runner_config_id}")
            db.add(
                ExpRunnerConfig(
                    id=runner.id,
                    name=runner.name,
                    model=runner.model,
                    reasoning_effort=runner.reasoning_effort,
                    speed_mode=runner.speed_mode,
                    timeout_seconds=runner.timeout_seconds,
                    config_sha256=runner.config_sha256,
                    frozen_runtime_json=runner.frozen_runtime_json,
                    status=runner.status,
                    created_at=runner.created_at,
                )
            )
    rubric = db.get(R23RubricVersion, source.rubric_version_id)
    if rubric is not None and db.get(ExpRubricVersion, rubric.id) is None:
        db.add(
            ExpRubricVersion(
                id=rubric.id,
                template_id=CASE_TEMPLATE_ID,
                name=rubric.name,
                rubric_text=rubric.rubric_text,
                # The historical hash is preserved verbatim; it was computed
                # with the r23 protocol hash formula, not the template one.
                rubric_sha256=rubric.rubric_sha256,
                status=rubric.status,
                created_at=rubric.created_at,
            )
        )
    db.flush()

    # --- project ---
    project = ExpProject(
        id=source.id,
        template_id=CASE_TEMPLATE_ID,
        name=source.name,
        kind=source.kind,
        status=source.status,
        dataset_revision_id=dataset_revision_id,
        rubric_version_id=source.rubric_version_id,
        pilot_project_id=source.pilot_project_id,
        formal_signature=source.formal_signature,
        data_processing_confirmed=source.data_processing_confirmed,
        data_processing_confirmed_at=source.data_processing_confirmed_at,
        data_status_json=source.data_status_json,
        manifest_json=source.manifest_json,
        manifest_sha256=source.manifest_sha256,
        results_embargoed=source.results_embargoed,
        report_sha256=source.report_sha256,
        read_only=True,
        source_system=SOURCE_SYSTEM,
        source_project_id=source.id,
        created_at=source.created_at,
        frozen_at=source.frozen_at,
        started_at=source.started_at,
        completed_at=source.completed_at,
        terminated_at=source.terminated_at,
    )
    db.add(project)
    db.flush()

    for binding in bindings:
        db.add(
            ExpProjectRunner(
                id=binding.id,
                project_id=project.id,
                runner_config_id=binding.runner_config_id,
                position=binding.position,
                model=binding.model,
                config_sha256=binding.config_sha256,
                frozen_runtime_json=binding.frozen_runtime_json or {},
            )
        )
    db.flush()

    # --- inputs (one row per unique input text) ---
    evaluations = db.scalars(
        select(R23UniqueEvaluation).where(R23UniqueEvaluation.project_id == source.id)
    ).all()
    observations = db.scalars(
        select(R23SampleObservation).where(R23SampleObservation.project_id == source.id)
    ).all()
    word_count_by_input: dict[str, int] = {}
    for observation in observations:
        word_count_by_input.setdefault(observation.input_sha256, observation.word_count)
    selection_kind = "pilot" if source.kind == "pilot_run" else "formal"
    prompt_sha_by_input: dict[str, str] = {}
    for observation in observations:
        prompt_sha_by_input.setdefault(
            observation.input_sha256, observation.prompt_sha256
        )
    input_id_by_key: dict[str, int] = {}
    # Inputs come from the union of evaluations (which carry the text) and
    # observations (which can reference an input whose evaluations were all
    # purged in edge cases).
    ordered_keys: list[str] = []
    for evaluation in evaluations:
        ordered_keys.append(evaluation.input_sha256)
    for observation in observations:
        ordered_keys.append(observation.input_sha256)
    text_by_input: dict[str, tuple[str, str]] = {}
    for evaluation in evaluations:
        text_by_input.setdefault(
            evaluation.input_sha256,
            (evaluation.prompt_text, evaluation.essay_text),
        )
    for key in dict.fromkeys(ordered_keys):
        prompt_text, essay_text = text_by_input.get(key, ("", ""))
        input_row = ExpInput(
            project_id=project.id,
            input_sha256=key,
            prompt_sha256=prompt_sha_by_input.get(key, ""),
            prompt_text=prompt_text,
            essay_text=essay_text,
            word_count=word_count_by_input.get(key, len(essay_text.split())),
            stratum=None,
            cell_key=None,
            inclusion_probability=1.0,
            design_weight=1.0,
            forced=False,
            selection_kind=selection_kind,
            provenance_json={},
        )
        db.add(input_row)
        input_id_by_key[key] = -1
    db.flush()
    for row in db.scalars(
        select(ExpInput).where(ExpInput.project_id == project.id)
    ).all():
        input_id_by_key[row.input_sha256] = row.id

    # Observation slots keep the CASE per-observation labels (colliding texts
    # can carry two labels for one channel; the slots are the source of truth).
    slot_id_by_key: dict[str, int] = {}
    for observation in observations:
        db.add(
            ExpObservationSlot(
                project_id=project.id,
                input_id=input_id_by_key[observation.input_sha256],
                slot_key=observation.observation_key,
                channel=observation.dimension,
                label_x2=observation.label_x2,
                provenance_json={
                    "source_id_hash": observation.source_id_hash,
                    "derived_base_id": observation.derived_base_id,
                    "corruption_repeat": observation.corruption_repeat,
                    "collision": observation.collision,
                    "rerun": observation.rerun,
                },
            )
        )
    db.flush()
    for row in db.scalars(
        select(ExpObservationSlot).where(ExpObservationSlot.project_id == project.id)
    ).all():
        slot_id_by_key[row.slot_key] = row.id
    labels_by_input: dict[int, dict[str, int]] = {}
    for observation in observations:
        input_id = input_id_by_key[observation.input_sha256]
        channels = labels_by_input.setdefault(input_id, {})
        channels.setdefault(observation.dimension, observation.label_x2)
    for input_id, channels in labels_by_input.items():
        for channel, label_x2 in channels.items():
            db.add(
                ExpInputLabel(input_id=input_id, channel=channel, label_x2=label_x2)
            )
    db.flush()

    # --- evaluations, scores, slots joins, attempts ---
    evaluation_id_map: dict[int, int] = {}
    for evaluation in evaluations:
        row = ExpUniqueEvaluation(
            project_id=project.id,
            binding_id=evaluation.model_binding_id,
            run_group_id=None,
            input_id=input_id_by_key[evaluation.input_sha256],
            input_sha256=evaluation.input_sha256,
            run_index=evaluation.run_index,
            status=evaluation.status,
            attempt_count=evaluation.attempt_count,
            latency_ms=evaluation.latency_ms,
            failure_code=evaluation.failure_code,
            failure_summary=evaluation.failure_summary,
            worker_id=None,
            lease_until=None,
            created_at=evaluation.created_at,
            completed_at=evaluation.completed_at,
        )
        db.add(row)
    db.flush()
    # Deterministic remap: match by (binding, input, run_index).
    imported_evaluations = db.scalars(
        select(ExpUniqueEvaluation).where(ExpUniqueEvaluation.project_id == project.id)
    ).all()
    key_by_imported = {
        (row.binding_id, row.input_sha256, row.run_index): row
        for row in imported_evaluations
    }
    for evaluation in evaluations:
        imported = key_by_imported[
            (evaluation.model_binding_id, evaluation.input_sha256, evaluation.run_index)
        ]
        evaluation_id_map[evaluation.id] = imported.id
    for evaluation in evaluations:
        new_id = evaluation_id_map[evaluation.id]
        for channel, value in (
            ("content", evaluation.content_score_x2),
            ("organization", evaluation.organization_score_x2),
            ("language", evaluation.language_score_x2),
        ):
            if value is not None:
                db.add(
                    ExpCallScore(evaluation_id=new_id, channel=channel, score_x2=value)
                )
    observation_id_map = {
        observation.id: slot_id_by_key[observation.observation_key]
        for observation in observations
    }
    joins = db.scalars(
        select(R23EvaluationObservation).where(
            R23EvaluationObservation.evaluation_id.in_(
                [item.id for item in evaluations]
            )
        )
    ).all()
    for join in joins:
        db.add(
            ExpEvaluationSlot(
                evaluation_id=evaluation_id_map[join.evaluation_id],
                slot_id=observation_id_map[join.observation_id],
            )
        )
    attempts = db.scalars(
        select(R23CallAttempt).where(R23CallAttempt.project_id == source.id)
    ).all()
    for attempt in attempts:
        db.add(
            ExpCallAttempt(
                project_id=project.id,
                evaluation_id=evaluation_id_map[attempt.evaluation_id],
                attempt_number=attempt.attempt_number,
                input_sha256=attempt.input_sha256,
                requested_model=attempt.requested_model,
                reasoning_effort=attempt.reasoning_effort,
                speed_mode=attempt.speed_mode,
                timeout_seconds=attempt.timeout_seconds,
                status=attempt.status,
                latency_ms=attempt.latency_ms,
                exit_code=attempt.exit_code,
                output_sha256=attempt.output_sha256,
                error_code=attempt.error_code,
                error_summary=attempt.error_summary,
                started_at=attempt.started_at,
            )
        )
    events = db.scalars(
        select(R23Event).where(R23Event.project_id == source.id)
    ).all()
    for event in events:
        db.add(
            ExpEvent(
                project_id=project.id,
                event_type=event.event_type,
                detail_json=event.detail_json,
                created_at=event.created_at,
            )
        )

    # --- locked report, verbatim ---
    report = db.get(R23LockedReport, source.id)
    report_match = False
    if report is not None:
        report_match = canonical_sha256(report.report_json) == report.report_sha256
        db.add(
            ExpLockedReport(
                project_id=project.id,
                report_json=report.report_json,
                report_sha256=report.report_sha256,
                figures_json=[
                    {
                        "name": "report_figure",
                        "svg": report.figure_svg,
                        "sha256": report.figure_sha256,
                    }
                ],
                created_at=report.created_at,
            )
        )

    imported_count = len(imported_evaluations)
    project.import_verification_json = _verification(
        source=source,
        counts={
            "evaluations": len(evaluations),
            "imported_evaluations": imported_count,
            "observations": len(observations),
            "attempts": len(attempts),
            "events": len(events),
        },
        report_sha256=report.report_sha256 if report is not None else None,
        report_match=report_match,
    )
    db.flush()


__all__ = ["SOURCE_SYSTEM", "import_legacy_r23"]
