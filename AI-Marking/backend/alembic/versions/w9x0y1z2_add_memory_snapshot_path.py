"""Add the recoverable filesystem path for committed memory snapshots.

Revision ID: w9x0y1z2
Revises: v8w9x0y1z2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "w9x0y1z2"
down_revision: Union[str, None] = "v8w9x0y1z2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("ms_memory_stores")}
    if "snapshot_path" not in columns:
        op.add_column("ms_memory_stores", sa.Column("snapshot_path", sa.String(length=512), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("ms_memory_stores")}
    if "snapshot_path" in columns:
        op.drop_column("ms_memory_stores", "snapshot_path")
