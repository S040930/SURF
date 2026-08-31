"""Isolated persistence for the r23 DREsS_CASE experiment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
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
from app.experiment.r23 import PROTOCOL_ID


class R23RunnerConfig(Base):
    __tablename__ = "r23_runner_configs"
    __table_args__ = (Index("ix_r23_runner_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(24), nullable=False)
    speed_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standard"
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_runtime_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R23RubricVersion(Base):
    __tablename__ = "r23_rubric_versions"
    __table_args__ = (Index("ix_r23_rubric_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(96), nullable=False, default=PROTOCOL_ID
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rubric_text: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R23Project(Base):
    __tablename__ = "r23_projects"
    __table_args__ = (
        UniqueConstraint("name", name="uq_r23_project_name"),
        Index("ix_r23_formal_signature", "formal_signature"),
        Index("ix_r23_project_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(96), nullable=False, default=PROTOCOL_ID
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    rubric_version_id: Mapped[str] = mapped_column(
        ForeignKey("r23_rubric_versions.id", ondelete="RESTRICT"), nullable=False
    )
    pilot_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="RESTRICT")
    )
    formal_signature: Mapped[str | None] = mapped_column(String(64))
    data_processing_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data_processing_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    data_status_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    results_embargoed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    report_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime)


class R23ModelBinding(Base):
    __tablename__ = "r23_model_bindings"
    __table_args__ = (
        UniqueConstraint("project_id", "position", name="uq_r23_model_position"),
        UniqueConstraint(
            "project_id", "runner_config_id", name="uq_r23_project_runner"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    runner_config_id: Mapped[str] = mapped_column(
        ForeignKey("r23_runner_configs.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class R23SampleObservation(Base):
    __tablename__ = "r23_sample_observations"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "observation_key", name="uq_r23_observation_key"
        ),
        Index("ix_r23_observation_dimension", "project_id", "dimension", "label_x2"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    observation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    derived_base_id: Mapped[str | None] = mapped_column(String(64))
    corruption_repeat: Mapped[int | None] = mapped_column(Integer)
    collision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rerun: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class R23UniqueEvaluation(Base):
    __tablename__ = "r23_unique_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "model_binding_id",
            "input_sha256",
            "run_index",
            name="uq_r23_unique_evaluation",
        ),
        Index("ix_r23_evaluation_lease", "status", "lease_until"),
        CheckConstraint(
            "content_score_x2 IS NULL OR content_score_x2 BETWEEN 2 AND 10"
        ),
        CheckConstraint(
            "organization_score_x2 IS NULL OR organization_score_x2 BETWEEN 2 AND 10"
        ),
        CheckConstraint(
            "language_score_x2 IS NULL OR language_score_x2 BETWEEN 2 AND 10"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_binding_id: Mapped[str] = mapped_column(
        ForeignKey("r23_model_bindings.id", ondelete="CASCADE"), nullable=False
    )
    run_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("r23_run_groups.id", ondelete="SET NULL")
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    essay_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_score_x2: Mapped[int | None] = mapped_column(Integer)
    organization_score_x2: Mapped[int | None] = mapped_column(Integer)
    language_score_x2: Mapped[int | None] = mapped_column(Integer)
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


class R23EvaluationObservation(Base):
    __tablename__ = "r23_evaluation_observations"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id", "observation_id", name="uq_r23_evaluation_observation"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("r23_unique_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("r23_sample_observations.id", ondelete="CASCADE"), nullable=False
    )


class R23CallAttempt(Base):
    __tablename__ = "r23_call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id", "attempt_number", name="uq_r23_attempt_number"
        ),
        Index("ix_r23_attempt_project", "project_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("r23_unique_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(24), nullable=False)
    speed_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standard"
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class R23RunGroup(Base):
    __tablename__ = "r23_run_groups"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "model_binding_id",
            "dimension",
            "run_index",
            name="uq_r23_run_group",
        ),
        Index("ix_r23_run_group_order", "project_id", "order_rank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_binding_id: Mapped[str] = mapped_column(
        ForeignKey("r23_model_bindings.id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    run_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class R23Event(Base):
    __tablename__ = "r23_events"
    __table_args__ = (Index("ix_r23_event_project", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R23LockedReport(Base):
    __tablename__ = "r23_locked_reports"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("r23_projects.id", ondelete="CASCADE"), primary_key=True
    )
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    figure_svg: Mapped[str] = mapped_column(Text, nullable=False)
    figure_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


__all__ = [name for name in globals() if name.startswith("R23")]
