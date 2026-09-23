"""Add cloud-embedding fields to ms_site_config.

Revision ID: a3b4c5d6e7f8
Revises: z2a3b4c5d6e7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "z2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = "ms_site_config"
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns(table)}
    additions = (
        sa.Column(
            "embedding_backend",
            sa.String(length=24),
            nullable=False,
            server_default="openai",
        ),
        sa.Column(
            "embedding_api_base",
            sa.String(length=400),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "embedding_api_key",
            sa.String(length=400),
            nullable=False,
            server_default="",
        ),
        sa.Column("embedding_dims", sa.Integer(), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(table, column)


def downgrade() -> None:
    table = "ms_site_config"
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns(table)}
    for name in (
        "embedding_dims",
        "embedding_api_key",
        "embedding_api_base",
        "embedding_backend",
    ):
        if name in columns:
            op.drop_column(table, name)
