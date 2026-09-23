"""Add v2 question shards and stream queue indexes."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b3c4d5e6f7g8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ms_question_runs" not in inspector.get_table_names():
        op.create_table(
            "ms_question_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("study_id", sa.String(length=36), nullable=False),
            sa.Column("question_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["study_id"], ["ms_studies.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("study_id", "question_id", name="uq_ms_question_run"),
        )
        op.create_index(
            "ix_ms_question_run_status",
            "ms_question_runs",
            ["study_id", "status", "question_id"],
        )
    indexes = {item["name"] for item in inspector.get_indexes("ms_calls")}
    if "ix_ms_call_stream_queue" not in indexes:
        op.create_index(
            "ix_ms_call_stream_queue",
            "ms_calls",
            ["study_id", "model", "condition", "question_id", "status", "id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("ms_calls")}
    if "ix_ms_call_stream_queue" in indexes:
        op.drop_index("ix_ms_call_stream_queue", table_name="ms_calls")
    if "ms_question_runs" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_ms_question_run_status", table_name="ms_question_runs")
        op.drop_table("ms_question_runs")
