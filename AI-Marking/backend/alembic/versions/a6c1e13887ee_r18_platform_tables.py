"""r18 platform tables

Revision ID: a6c1e13887ee
Revises: (root — the historical r13/r16/r17 schema chain was removed)
Create Date: 2026-08-05 22:40:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6c1e13887ee"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "r18_prompt_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("templates_json", sa.JSON(), nullable=False),
        sa.Column("templates_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_r18_prompt_status", "r18_prompt_versions", ["status"])

    op.create_table(
        "r18_prompt_trials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "prompt_version_id",
            sa.String(length=36),
            sa.ForeignKey("r18_prompt_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("template_kind", sa.String(length=32), nullable=False),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("model_config_json", sa.JSON(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("freeze_decision", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_r18_trial_version", "r18_prompt_trials", ["prompt_version_id"])

    op.create_table(
        "r18_model_configs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_r18_model_status", "r18_model_configs", ["status"])

    op.create_table(
        "r18_projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=True),
        sa.Column("model_config_json", sa.JSON(), nullable=True),
        sa.Column("model_config_sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_version_id", sa.String(length=36), nullable=True),
        sa.Column("prompt_templates_json", sa.JSON(), nullable=True),
        sa.Column("prompt_version_sha256", sa.String(length=64), nullable=True),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_sha256", sa.String(length=64), nullable=False),
        sa.Column("no_answer_key_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("terminated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("name", name="uq_r18_project_name"),
    )
    op.create_index("ix_r18_project_status", "r18_projects", ["status"])

    op.create_table(
        "r18_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("student_answer", sa.Text(), nullable=False),
        sa.Column("teacher_grade", sa.Integer(), nullable=False),
        sa.Column("teacher_feedback", sa.Text(), nullable=False),
        sa.Column("usage", sa.String(length=16), nullable=False),
        sa.Column("score_stratum", sa.String(length=8), nullable=False),
        sa.Column("duplicate_cluster", sa.String(length=64), nullable=True),
        sa.Column("test_position", sa.Integer(), nullable=True),
        sa.Column("memory_position", sa.Integer(), nullable=True),
        sa.Column("source_position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("project_id", "entry_id", name="uq_r18_record_entry"),
    )
    op.create_index(
        "ix_r18_record_question", "r18_records", ["project_id", "question_id"]
    )

    op.create_table(
        "r18_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "question_id", "path", "position", name="uq_r18_order_pos"
        ),
    )

    op.create_table(
        "r18_streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("condition", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "question_id",
            "condition",
            "path",
            name="uq_r18_stream",
        ),
    )
    op.create_index("ix_r18_stream_lease", "r18_streams", ["status", "lease_until"])

    op.create_table(
        "r18_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("condition", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=False),
        sa.Column("history_count", sa.Integer(), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("source_record_ids_json", sa.JSON(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "question_id",
            "condition",
            "path",
            "history_count",
            name="uq_r18_snapshot",
        ),
    )

    op.create_table(
        "r18_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("condition", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=False),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("history_count", sa.Integer(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("model_score", sa.Integer(), nullable=True),
        sa.Column("teacher_score", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_r18_call_project", "r18_calls", ["project_id", "status"])
    op.create_index("ix_r18_call_kind", "r18_calls", ["project_id", "kind"])

    op.create_table(
        "r18_call_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "call_id",
            sa.Integer(),
            sa.ForeignKey("r18_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("model_identity", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
    )

    op.create_table(
        "r18_analysis_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("core_json", sa.JSON(), nullable=False),
        sa.Column("supplement_json", sa.JSON(), nullable=False),
        sa.Column("analysis_version", sa.String(length=32), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "r18_workers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "r18_workers",
        "r18_analysis_results",
        "r18_call_attempts",
        "r18_calls",
        "r18_snapshots",
        "r18_streams",
        "r18_orders",
        "r18_records",
        "r18_projects",
        "r18_model_configs",
        "r18_prompt_trials",
        "r18_prompt_versions",
    ):
        op.drop_table(table)
