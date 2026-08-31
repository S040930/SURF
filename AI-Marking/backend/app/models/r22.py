"""Persistence for r22 overflow-recovery audit records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
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
from app.experiment.r22 import PROTOCOL_ID


class R22CompressionAudit(Base):
    """One immutable primary or compression stage for a logical r22 call."""

    __tablename__ = "r22_compression_audits"
    __table_args__ = (
        Index("ix_r22_compression_audit_project", "project_id", "created_at"),
        Index("ix_r22_compression_audit_outcome", "project_id", "outcome"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    protocol_id: Mapped[str] = mapped_column(
        String(96), nullable=False, default=PROTOCOL_ID
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    logical_call_key: Mapped[str] = mapped_column(String(320), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    condition: Mapped[str | None] = mapped_column(String(8))
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    original_json: Mapped[dict | None] = mapped_column(JSON)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    original_sha256: Mapped[str | None] = mapped_column(String(64))
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    original_token_counts_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    output_token_counts_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    violations_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    prompt_version: Mapped[str | None] = mapped_column(String(96))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class R22Call(Base):
    """One logical primary call in the 40/10 pilot schedule."""

    __tablename__ = "r22_calls"
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
            name="uq_r22_call_key",
        ),
        Index("ix_r22_call_project_status", "project_id", "status"),
        Index("ix_r22_call_training_curve", "project_id", "kind", "history_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    trajectory: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    answer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    history_count: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    model_score: Mapped[float | None] = mapped_column(Float)
    teacher_score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
