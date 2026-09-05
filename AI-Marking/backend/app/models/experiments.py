"""Generic persistence for the unified experiment platform.

Every registered research template stores its projects, inputs, calls, and
reports in these ``exp_`` tables; dataset-specific rules live in the scoring
contract and the template, never in the schema.  The frozen r23 tables stay
untouched and are imported read-only by the legacy importer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class ExpDataset(Base):
    __tablename__ = "exp_datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    access_level: Mapped[str] = mapped_column(String(24), nullable=False)
    license_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpDatasetRevision(Base):
    __tablename__ = "exp_dataset_revisions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "revision_label", name="uq_exp_revision_label"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("exp_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    revision_label: Mapped[str] = mapped_column(String(96), nullable=False)
    file_specs_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    audit_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpScoringContract(Base):
    __tablename__ = "exp_scoring_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_revision_id: Mapped[str] = mapped_column(
        ForeignKey("exp_dataset_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    channels_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    grid_min_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_max_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpRubricVersion(Base):
    __tablename__ = "exp_rubric_versions"
    __table_args__ = (Index("ix_exp_rubric_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rubric_text: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpRunnerConfig(Base):
    __tablename__ = "exp_runner_configs"
    __table_args__ = (Index("ix_exp_runner_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(24), nullable=False)
    speed_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_runtime_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpProject(Base):
    __tablename__ = "exp_projects"
    __table_args__ = (
        UniqueConstraint("name", name="uq_exp_project_name"),
        UniqueConstraint("source_system", "source_project_id", name="uq_exp_project_source"),
        Index("ix_exp_project_status", "status"),
        Index("ix_exp_project_template", "template_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    dataset_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("exp_dataset_revisions.id", ondelete="RESTRICT")
    )
    rubric_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("exp_rubric_versions.id", ondelete="RESTRICT")
    )
    pilot_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="RESTRICT")
    )
    formal_signature: Mapped[str | None] = mapped_column(String(64))
    data_processing_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data_processing_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    data_status_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    results_embargoed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64))
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="core")
    source_project_id: Mapped[str | None] = mapped_column(String(36))
    import_verification_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime)


class ExpProjectRunner(Base):
    __tablename__ = "exp_project_runners"
    __table_args__ = (
        UniqueConstraint("project_id", "position", name="uq_exp_binding_position"),
        UniqueConstraint("project_id", "runner_config_id", name="uq_exp_binding_runner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    runner_config_id: Mapped[str] = mapped_column(
        ForeignKey("exp_runner_configs.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ExpInput(Base):
    """One restricted input stored exactly once per project.

    Essay text lives here and only here; evaluations reference the row by id.
    """

    __tablename__ = "exp_inputs"
    __table_args__ = (
        UniqueConstraint("project_id", "input_sha256", name="uq_exp_input_key"),
        Index("ix_exp_input_selection", "project_id", "selection_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    essay_text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stratum: Mapped[int | None] = mapped_column(Integer)
    cell_key: Mapped[str | None] = mapped_column(String(96))
    inclusion_probability: Mapped[float | None] = mapped_column(Float)
    design_weight: Mapped[float | None] = mapped_column(Float)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selection_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ExpInputLabel(Base):
    __tablename__ = "exp_input_labels"
    __table_args__ = (
        UniqueConstraint("input_id", "channel", name="uq_exp_input_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    input_id: Mapped[int] = mapped_column(
        ForeignKey("exp_inputs.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(48), nullable=False)
    label_x2: Mapped[int] = mapped_column(Integer, nullable=False)


class ExpRunGroup(Base):
    __tablename__ = "exp_run_groups"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "binding_id",
            "group_key",
            "run_index",
            name="uq_exp_run_group",
        ),
        Index("ix_exp_run_group_order", "project_id", "order_rank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("exp_project_runners.id", ondelete="CASCADE"), nullable=False
    )
    group_key: Mapped[str] = mapped_column(String(48), nullable=False)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ExpUniqueEvaluation(Base):
    __tablename__ = "exp_unique_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "binding_id",
            "input_sha256",
            "run_index",
            name="uq_exp_unique_evaluation",
        ),
        Index("ix_exp_evaluation_lease", "status", "lease_until"),
        CheckConstraint("run_index >= 0", name="ck_exp_run_index_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("exp_project_runners.id", ondelete="CASCADE"), nullable=False
    )
    run_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("exp_run_groups.id", ondelete="SET NULL")
    )
    input_id: Mapped[int] = mapped_column(
        ForeignKey("exp_inputs.id", ondelete="RESTRICT"), nullable=False
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ExpObservationSlot(Base):
    """One dataset observation owned by an input (observation-slot templates).

    Many slots can share one evaluation: the unit of analysis is the dataset
    observation, while the unit of execution is the deduplicated logical call.
    """

    __tablename__ = "exp_observation_slots"
    __table_args__ = (
        UniqueConstraint("project_id", "slot_key", name="uq_exp_slot_key"),
        Index("ix_exp_slot_channel", "project_id", "channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    input_id: Mapped[int] = mapped_column(
        ForeignKey("exp_inputs.id", ondelete="RESTRICT"), nullable=False
    )
    slot_key: Mapped[str] = mapped_column(String(96), nullable=False)
    channel: Mapped[str] = mapped_column(String(48), nullable=False)
    label_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ExpEvaluationSlot(Base):
    __tablename__ = "exp_evaluation_slots"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "slot_id", name="uq_exp_evaluation_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("exp_observation_slots.id", ondelete="CASCADE"), nullable=False
    )


class ExpCallScore(Base):
    """One channel score per row; the contract defines the legal channel set."""

    __tablename__ = "exp_call_scores"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "channel", name="uq_exp_call_score"),
        CheckConstraint(
            "score_x2 IS NULL OR score_x2 BETWEEN 1 AND 10",
            name="ck_exp_score_x2_grid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(48), nullable=False)
    score_x2: Mapped[int] = mapped_column(Integer, nullable=False)


class ExpCallAttempt(Base):
    __tablename__ = "exp_call_attempts"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "attempt_number", name="uq_exp_attempt_number"),
        Index("ix_exp_attempt_project", "project_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(24), nullable=False)
    speed_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExpEvent(Base):
    __tablename__ = "exp_events"
    __table_args__ = (Index("ix_exp_event_project", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class ExpLockedReport(Base):
    __tablename__ = "exp_locked_reports"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("exp_projects.id", ondelete="CASCADE"), primary_key=True
    )
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    figures_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


__all__ = [name for name in globals() if name.startswith("Exp")]
