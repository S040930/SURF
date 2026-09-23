"""Make ms_runner_configs primary key (model, speed_mode).

Revision ID: z2a3b4c5d6e7
Revises: y1z2a3b4c5d6
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "z2a3b4c5d6e7"
down_revision: Union[str, None] = "y1z2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    constraint = inspect(bind).get_pk_constraint("ms_runner_configs")
    if constraint.get("constrained_columns") != ["model", "speed_mode"]:
        op.drop_constraint("ms_runner_configs_pkey", "ms_runner_configs", type_="primary")
        op.create_primary_key(
            "ms_runner_configs_pkey", "ms_runner_configs", ["model", "speed_mode"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    constraint = inspect(bind).get_pk_constraint("ms_runner_configs")
    if constraint.get("constrained_columns") != ["model"]:
        op.drop_constraint("ms_runner_configs_pkey", "ms_runner_configs", type_="primary")
        op.create_primary_key("ms_runner_configs_pkey", "ms_runner_configs", ["model"])
