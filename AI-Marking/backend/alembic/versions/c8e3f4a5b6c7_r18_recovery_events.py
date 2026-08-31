"""Add auditable r18 manual recovery events.

Revision ID: c8e3f4a5b6c7
Revises: b7d2e3f4a5b6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e3f4a5b6c7"
down_revision: Union[str, None] = "b7d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "r18_recovery_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "call_id",
            sa.Integer(),
            sa.ForeignKey("r18_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_error_type", sa.String(length=120), nullable=False),
        sa.Column("original_failure_reason", sa.Text(), nullable=False),
        sa.Column("released_blocked_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_r18_recovery_project", "r18_recovery_events", ["project_id", "created_at"])
    op.create_index("ix_r18_recovery_call", "r18_recovery_events", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_r18_recovery_call", table_name="r18_recovery_events")
    op.drop_index("ix_r18_recovery_project", table_name="r18_recovery_events")
    op.drop_table("r18_recovery_events")
