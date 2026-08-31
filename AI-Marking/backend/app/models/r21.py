"""Persistence for the r21 Codex CLI experiment protocol."""

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
from app.experiment.r21 import PROTOCOL_ID


class R21RunnerRuntime(Base):
    """Singleton lease proving that an MCP-hosted worker is connected."""

    __tablename__ = "r21_runner_runtime"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class R21RunnerConfig(Base):
    __tablename__ = "r21_runner_configs"
    __table_args__ = (Index("ix_r21_runner_status", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_runtime_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R21PromptVersion(Base):
    __tablename__ = "r21_prompt_versions"
    __table_args__ = (Index("ix_r21_prompt_status", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(96), nullable=False, default=PROTOCOL_ID
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    templates_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    templates_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R21Project(Base):
    __tablename__ = "r21_projects"
    __table_args__ = (
        Index("ix_r21_project_status", "status"),
        UniqueConstraint("name", name="uq_r21_project_name"),
        UniqueConstraint("formal_signature", name="uq_r21_formal_signature"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(96), nullable=False, default=PROTOCOL_ID
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    runner_config_id: Mapped[str] = mapped_column(
        ForeignKey("r21_runner_configs.id", ondelete="RESTRICT"), nullable=False
    )
    runner_config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    runner_config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt_version_id: Mapped[str] = mapped_column(
        ForeignKey("r21_prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_templates_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pilot_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="RESTRICT")
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


class R21Record(Base):
    __tablename__ = "r21_records"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "answer_id", "usage", "trajectory", name="uq_r21_record_usage"
        ),
        Index("ix_r21_record_question", "project_id", "question_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
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


class R21Stream(Base):
    __tablename__ = "r21_streams"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "question_id", "condition", "trajectory", name="uq_r21_stream"
        ),
        Index("ix_r21_stream_lease", "status", "lease_until"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False)
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    worker_id: Mapped[str | None] = mapped_column(String(64))


class R21RunGroup(Base):
    __tablename__ = "r21_run_groups"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "question_id", "condition", name="uq_r21_run_group"
        ),
        Index("ix_r21_run_group_active", "project_id", "status", "order_rank"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    order_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class R21Snapshot(Base):
    __tablename__ = "r21_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "question_id",
            "condition",
            "trajectory",
            "history_count",
            name="uq_r21_snapshot",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
    )
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


class R21Call(Base):
    __tablename__ = "r21_calls"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "question_id",
            "condition",
            "trajectory",
            "kind",
            "answer_id",
            "history_count",
            "repeat",
            name="uq_r21_call_key",
        ),
        Index("ix_r21_call_project", "project_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
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
    teacher_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R21CallAttempt(Base):
    __tablename__ = "r21_call_attempts"
    __table_args__ = (
        UniqueConstraint("call_id", "attempt_number", name="uq_r21_call_attempt"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("r21_calls.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(24), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(128))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    stderr_excerpt: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class R21Report(Base):
    __tablename__ = "r21_reports"
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), primary_key=True
    )
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R21Exposure(Base):
    __tablename__ = "r21_exposures"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("r21_projects.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
