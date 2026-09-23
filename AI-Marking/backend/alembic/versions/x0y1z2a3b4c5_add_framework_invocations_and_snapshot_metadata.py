"""Record official-framework LLM calls and validate native snapshots.

Revision ID: x0y1z2a3b4c5
Revises: w9x0y1z2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "x0y1z2a3b4c5"
down_revision: Union[str, None] = "w9x0y1z2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    store_columns = {column["name"] for column in inspector.get_columns("ms_memory_stores")}
    additions = {
        "snapshot_artifact_sha256": sa.Column("snapshot_artifact_sha256", sa.String(length=64), nullable=True),
        "framework_version": sa.Column("framework_version", sa.String(length=160), nullable=True),
        "snapshot_stream_id": sa.Column("snapshot_stream_id", sa.String(length=160), nullable=True),
    }
    for name, column in additions.items():
        if name not in store_columns:
            op.add_column("ms_memory_stores", column)

    from app.db.base import Base

    Base.metadata.tables["ms_framework_invocations"].create(
        op.get_bind(), checkfirst=True
    )


def downgrade() -> None:
    from app.db.base import Base

    Base.metadata.tables["ms_framework_invocations"].drop(
        op.get_bind(), checkfirst=True
    )
    inspector = inspect(op.get_bind())
    store_columns = {column["name"] for column in inspector.get_columns("ms_memory_stores")}
    for name in (
        "snapshot_stream_id",
        "framework_version",
        "snapshot_artifact_sha256",
    ):
        if name in store_columns:
            op.drop_column("ms_memory_stores", name)
