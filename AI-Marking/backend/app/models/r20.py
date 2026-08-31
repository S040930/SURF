"""Persistence for the isolated r20 SAF official-split protocol."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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


class R20ModelConfig(Base):
    __tablename__ = "r20_model_configs"
    __table_args__ = (Index("ix_r20_model_status", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R20PromptVersion(Base):
    __tablename__ = "r20_prompt_versions"
    __table_args__ = (Index("ix_r20_prompt_status", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="r20-saf-official-split-2026-08-v4-8q-60m-15t",
    )
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("r20_prompt_versions.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    templates_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    templates_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validated_model_config_ids_json: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R20PromptTrial(Base):
    __tablename__ = "r20_prompt_trials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_id: Mapped[str | None] = mapped_column(
        ForeignKey("r20_prompt_validation_suites.id", ondelete="CASCADE")
    )
    # Stored as an immutable copied identifier. The API verifies the owned
    # prompt row before creation while migrations remain append-only.
    prompt_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_config_id: Mapped[str] = mapped_column(
        ForeignKey("r20_model_configs.id", ondelete="RESTRICT"), nullable=False
    )
    template_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    condition: Mapped[str | None] = mapped_column(String(8))
    scenario: Mapped[str | None] = mapped_column(String(32))
    order_index: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R20PromptValidationSuite(Base):
    __tablename__ = "r20_prompt_validation_suites"
    __table_args__ = (Index("ix_r20_prompt_suite_status", "status", "lease_until"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version_id: Mapped[str] = mapped_column(
        ForeignKey("r20_prompt_versions.id", ondelete="CASCADE"), nullable=False
    )
    model_config_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    expected_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=48)
    completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class R20Project(Base):
    __tablename__ = "r20_projects"
    __table_args__ = (
        Index("ix_r20_project_status", "status"),
        UniqueConstraint("name", name="uq_r20_project_name"),
        UniqueConstraint("formal_signature", name="uq_r20_formal_signature"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="r20-saf-official-split-2026-08-v4-8q-60m-15t",
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    model_config_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    model_configs_json: Mapped[list] = mapped_column(JSON, nullable=False)
    model_configs_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version_id: Mapped[str] = mapped_column(
        ForeignKey("r20_prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_templates_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pilot_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="RESTRICT")
    )
    formal_signature: Mapped[str | None] = mapped_column(String(64))
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime)


class R20Record(Base):
    __tablename__ = "r20_records"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "answer_id", "usage", "trajectory", name="uq_r20_record_usage"
        ),
        Index("ix_r20_record_question", "project_id", "question_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False)
    student_answer: Mapped[str] = mapped_column(Text, nullable=False)
    teacher_score: Mapped[float] = mapped_column(Float, nullable=False)
    teacher_feedback: Mapped[str] = mapped_column(Text, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    source_split: Mapped[str] = mapped_column(String(32), nullable=False)
    usage: Mapped[str] = mapped_column(String(16), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    probe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class R20Stream(Base):
    __tablename__ = "r20_streams"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "model_id",
            "question_id",
            "condition",
            "trajectory",
            name="uq_r20_stream",
        ),
        Index("ix_r20_stream_lease", "status", "lease_until"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False)
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    worker_id: Mapped[str | None] = mapped_column(String(64))


class R20RunGroup(Base):
    """A runnable cell of the grid: one question under one condition.

    Created at freeze time (one row per question x condition).  The worker
    leases streams only from the single active group, so the experiment runs
    one cell at a time while staying blind until every group completes.
    """

    __tablename__ = "r20_run_groups"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "question_id",
            "condition",
            name="uq_r20_run_group",
        ),
        Index("ix_r20_run_group_active", "project_id", "status", "order_rank"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class R20Snapshot(Base):
    __tablename__ = "r20_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "model_id",
            "question_id",
            "condition",
            "trajectory",
            "history_count",
            name="uq_r20_snapshot",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False)
    history_count: Mapped[int] = mapped_column(Integer, nullable=False)
    items_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_record_ids_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    visible_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_token_count: Mapped[int] = mapped_column(Integer, nullable=False)


class R20Call(Base):
    __tablename__ = "r20_calls"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "model_id",
            "question_id",
            "condition",
            "trajectory",
            "kind",
            "answer_id",
            "history_count",
            "repeat",
            name="uq_r20_call_grid",
        ),
        Index("ix_r20_call_project", "project_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False)
    history_count: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    model_score: Mapped[float | None] = mapped_column(Float)
    teacher_score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R20CallAttempt(Base):
    __tablename__ = "r20_call_attempts"
    __table_args__ = (
        UniqueConstraint("call_id", "attempt_number", name="uq_r20_call_attempt"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("r20_calls.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    returned_model: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    system_fingerprint: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class R20Report(Base):
    __tablename__ = "r20_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, nullable=False
    )


class R20Exposure(Base):
    __tablename__ = "r20_exposures"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r20_projects.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, nullable=False
    )
