"""Persistence for the isolated SAF memory-framework study."""

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


class MSStudy(Base):
    __tablename__ = "ms_studies"
    __table_args__ = (
        UniqueConstraint("name", name="uq_ms_study_name"),
        Index("ix_ms_study_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    data_processing_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data_manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preflight_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    integrity_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    integrity_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    results_embargoed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime)


class MSQuestionRun(Base):
    """Independent execution shard for one selected formal question."""

    __tablename__ = "ms_question_runs"
    __table_args__ = (
        UniqueConstraint("study_id", "question_id", name="uq_ms_question_run"),
        Index("ix_ms_question_run_status", "study_id", "status", "question_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )


class MSRecord(Base):
    """Restricted local copy of a selected SAF answer."""

    __tablename__ = "ms_records"
    __table_args__ = (
        UniqueConstraint("study_id", "answer_id", name="uq_ms_record_answer"),
        Index("ix_ms_record_question", "study_id", "question_id", "selection_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False)
    student_answer: Mapped[str] = mapped_column(Text, nullable=False)
    teacher_score: Mapped[float] = mapped_column(Float, nullable=False)
    teacher_feedback: Mapped[str] = mapped_column(Text, nullable=False)
    verification_feedback: Mapped[str] = mapped_column(Text, nullable=False)
    source_split: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str] = mapped_column(String(240), nullable=False)
    source_position: Mapped[int] = mapped_column(Integer, nullable=False)


class MSMemoryStore(Base):
    __tablename__ = "ms_memory_stores"
    __table_args__ = (
        UniqueConstraint(
            "study_id",
            "model",
            "question_id",
            "condition",
            "order_variant",
            name="uq_ms_memory_store",
        ),
        Index("ix_ms_memory_store_status", "study_id", "status"),
        Index(
            "ix_ms_memory_store_model_status",
            "study_id",
            "model",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    framework: Mapped[str] = mapped_column(String(24), nullable=False)
    condition: Mapped[str] = mapped_column(String(40), nullable=False)
    feedback_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    order_variant: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    committed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    history_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_path: Mapped[str | None] = mapped_column(String(512))
    snapshot_artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(160))
    snapshot_stream_id: Mapped[str | None] = mapped_column(String(160))
    last_call_id: Mapped[int | None] = mapped_column(Integer)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    slot_id: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )


class MSCall(Base):
    __tablename__ = "ms_calls"
    __table_args__ = (
        UniqueConstraint(
            "study_id",
            "model",
            "question_id",
            "condition",
            "order_variant",
            "kind",
            "answer_id",
            "repeat",
            name="uq_ms_call_logical",
        ),
        Index("ix_ms_call_queue", "study_id", "status", "kind"),
        Index("ix_ms_call_model_queue", "study_id", "model", "status", "id"),
        Index(
            "ix_ms_call_stream_queue",
            "study_id",
            "model",
            "condition",
            "question_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False
    )
    memory_store_id: Mapped[int | None] = mapped_column(
        ForeignKey("ms_memory_stores.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(40), nullable=False)
    framework: Mapped[str] = mapped_column(String(24), nullable=False)
    feedback_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    order_variant: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_json: Mapped[dict | None] = mapped_column(JSON)
    retrieval_json: Mapped[list | None] = mapped_column(JSON)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    memory_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    manual_score: Mapped[float | None] = mapped_column(Float)
    model_score: Mapped[float | None] = mapped_column(Float)
    signed_diff: Mapped[float | None] = mapped_column(Float)
    absolute_diff: Mapped[float | None] = mapped_column(Float)
    normalized_signed_diff: Mapped[float | None] = mapped_column(Float)
    normalized_absolute_diff: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    slot_id: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    # 指数退避重试的到期时间。pending 且 retry_after 在未来的调用不可领取；
    # 已到期或为 NULL 的 pending 正常进入候选。领取/终态后清空。
    retry_after: Mapped[datetime | None] = mapped_column(DateTime)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MSCallAttempt(Base):
    __tablename__ = "ms_call_attempts"
    __table_args__ = (
        UniqueConstraint("call_id", "attempt_number", name="uq_ms_call_attempt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("ms_calls.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Keep the bounded CLI stderr excerpt separate from the human-readable
    # error message.  This makes every attempt independently auditable without
    # retaining an unbounded subprocess buffer or a sensitive prompt.
    stderr_excerpt: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MSFrameworkInvocation(Base):
    """Ledger row for one LLM request made inside an official framework."""

    __tablename__ = "ms_framework_invocations"
    __table_args__ = (
        Index("ix_ms_framework_invocation_call", "call_id", "attempt_number"),
        UniqueConstraint(
            "call_id",
            "attempt_number",
            "sequence_number",
            name="uq_ms_framework_invocation_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("ms_calls.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    framework: Mapped[str] = mapped_column(String(24), nullable=False)
    phase: Mapped[str] = mapped_column(String(80), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(String(32))
    cli_fingerprint_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MSAuditEvent(Base):
    __tablename__ = "ms_audit_events"
    __table_args__ = (Index("ix_ms_audit_event_study", "study_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class MSReport(Base):
    __tablename__ = "ms_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(
        ForeignKey("ms_studies.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class MSSchedulerRuntime(Base):
    __tablename__ = "ms_scheduler_runtime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="offline")
    max_subprocesses: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    active_subprocesses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slots_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    # 租约持有进程的身份：崩溃后残留的活租约可据此判定持有者已死，
    # 新 worker 立即接管而不必等 600s 租约自然过期。
    worker_pid: Mapped[int | None] = mapped_column(Integer)
    worker_host: Mapped[str | None] = mapped_column(String(255))
    runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class MSRunnerConfig(Base):
    """Site defaults copied into a project's immutable runtime snapshot.

    Editing a row affects only projects created/frozen afterwards.  A running
    study never reads these rows; each model may carry both ``standard`` and
    ``fast`` variants.
    """

    __tablename__ = "ms_runner_configs"

    model: Mapped[str] = mapped_column(String(160), primary_key=True)
    speed_mode: Mapped[str] = mapped_column(
        String(24), primary_key=True, default="standard"
    )
    reasoning_effort: Mapped[str] = mapped_column(String(32), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )


class MSSiteConfig(Base):
    """Single-row site config; overrides the embedding env defaults.

    ``embedding_backend`` selects the retrieval channel.  The active SAF
    protocol uses ``openai`` to call an OpenAI-compatible endpoint (base URL +
    key); the remaining fields are its immutable revision metadata.
    """

    __tablename__ = "ms_site_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_backend: Mapped[str] = mapped_column(
        String(24), nullable=False, default="openai"
    )
    embedding_api_base: Mapped[str] = mapped_column(
        String(400), nullable=False, default=""
    )
    embedding_api_key: Mapped[str] = mapped_column(
        String(400), nullable=False, default=""
    )
    embedding_dims: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )


__all__ = [
    "MSAuditEvent",
    "MSCall",
    "MSCallAttempt",
    "MSFrameworkInvocation",
    "MSMemoryStore",
    "MSRecord",
    "MSReport",
    "MSRunnerConfig",
    "MSSchedulerRuntime",
    "MSSiteConfig",
    "MSStudy",
]
