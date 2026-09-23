"""Add runtime preflight evidence and model-scoped queue indexes."""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    study_columns = {column["name"] for column in inspector.get_columns("ms_studies")}
    if "preflight_json" not in study_columns:
        op.add_column(
            "ms_studies",
            sa.Column(
                "preflight_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
        op.alter_column("ms_studies", "preflight_json", server_default=None)
    attempt_columns = {
        column["name"]
        for column in inspector.get_columns("ms_call_attempts")
    }
    if "stderr_excerpt" not in attempt_columns:
        op.add_column(
            "ms_call_attempts",
            sa.Column("stderr_excerpt", sa.Text(), nullable=True),
        )
    indexes = {
        index["name"]
        for table in ("ms_memory_stores", "ms_calls")
        for index in inspector.get_indexes(table)
    }
    if "ix_ms_memory_store_model_status" not in indexes:
        op.create_index(
            "ix_ms_memory_store_model_status",
            "ms_memory_stores",
            ["study_id", "model", "status"],
        )
    if "ix_ms_call_model_queue" not in indexes:
        op.create_index(
            "ix_ms_call_model_queue",
            "ms_calls",
            ["study_id", "model", "status", "id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for name, table in (
        ("ix_ms_call_model_queue", "ms_calls"),
        ("ix_ms_memory_store_model_status", "ms_memory_stores"),
    ):
        if name in {index["name"] for index in inspector.get_indexes(table)}:
            op.drop_index(name, table_name=table)
    if "preflight_json" in {
        column["name"] for column in inspector.get_columns("ms_studies")
    }:
        op.drop_column("ms_studies", "preflight_json")
    if "stderr_excerpt" in {
        column["name"] for column in inspector.get_columns("ms_call_attempts")
    }:
        op.drop_column("ms_call_attempts", "stderr_excerpt")
