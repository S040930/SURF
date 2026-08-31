"""Add auditable r23 runner speed mode.

Revision ID: s4i5j6k7l8m9
Revises: r3h4i5j6k7l8
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "s4i5j6k7l8m9"
down_revision: Union[str, None] = "r3h4i5j6k7l8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in ("r23_runner_configs", "r23_call_attempts"):
        if "speed_mode" not in _columns(table):
            op.add_column(
                table,
                sa.Column(
                    "speed_mode",
                    sa.String(length=16),
                    nullable=False,
                    server_default="standard",
                ),
            )


def downgrade() -> None:
    for table in ("r23_call_attempts", "r23_runner_configs"):
        if "speed_mode" in _columns(table):
            op.drop_column(table, "speed_mode")
