"""r20 v2 protocol isolation and prompt validation suites.

Revision ID: m8c9d0e1f2a3
Revises: l7b8c9d0e1f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m8c9d0e1f2a3"
down_revision: str | None = "l7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V1 = "r20-saf-official-split-2026-08-v1"


def upgrade() -> None:
    op.add_column(
        "r20_prompt_versions",
        sa.Column("protocol_id", sa.String(length=80), nullable=False, server_default=V1),
    )
    op.add_column(
        "r20_prompt_versions",
        sa.Column("validated_model_config_ids_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "r20_projects",
        sa.Column("protocol_id", sa.String(length=80), nullable=False, server_default=V1),
    )
    op.create_table(
        "r20_prompt_validation_suites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("protocol_id", sa.String(length=80), nullable=False),
        sa.Column(
            "prompt_version_id",
            sa.String(length=36),
            sa.ForeignKey("r20_prompt_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_config_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("expected_calls", sa.Integer(), nullable=False),
        sa.Column("completed_calls", sa.Integer(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_r20_prompt_suite_status",
        "r20_prompt_validation_suites",
        ["status", "lease_until"],
    )
    op.add_column(
        "r20_prompt_trials",
        sa.Column(
            "suite_id",
            sa.String(length=36),
            sa.ForeignKey("r20_prompt_validation_suites.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "r20_prompt_trials", sa.Column("condition", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "r20_prompt_trials", sa.Column("scenario", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "r20_prompt_trials", sa.Column("order_index", sa.Integer(), nullable=True)
    )
    op.add_column(
        "r20_prompt_trials",
        sa.Column("status", sa.String(length=24), nullable=False, server_default="success"),
    )
    op.execute("UPDATE r20_prompt_trials SET status = CASE WHEN valid THEN 'success' ELSE 'failed' END")


def downgrade() -> None:
    op.drop_column("r20_prompt_trials", "status")
    op.drop_column("r20_prompt_trials", "order_index")
    op.drop_column("r20_prompt_trials", "scenario")
    op.drop_column("r20_prompt_trials", "condition")
    op.drop_column("r20_prompt_trials", "suite_id")
    op.drop_index("ix_r20_prompt_suite_status", table_name="r20_prompt_validation_suites")
    op.drop_table("r20_prompt_validation_suites")
    op.drop_column("r20_projects", "protocol_id")
    op.drop_column("r20_prompt_versions", "validated_model_config_ids_json")
    op.drop_column("r20_prompt_versions", "protocol_id")
