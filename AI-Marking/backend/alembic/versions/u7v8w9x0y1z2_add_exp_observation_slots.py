"""Add observation slots for observation-level analysis templates.

Revision ID: u7v8w9x0y1z2
Revises: t5u6v7w8x9y0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "u7v8w9x0y1z2"
down_revision: Union[str, None] = "t5u6v7w8x9y0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()
    if "exp_observation_slots" not in existing:
        op.create_table(
            "exp_observation_slots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "input_id",
                sa.Integer(),
                sa.ForeignKey("exp_inputs.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("slot_key", sa.String(length=96), nullable=False),
            sa.Column("channel", sa.String(length=48), nullable=False),
            sa.Column("label_x2", sa.Integer(), nullable=False),
            sa.Column("provenance_json", sa.JSON(), nullable=False),
            sa.UniqueConstraint("project_id", "slot_key", name="uq_exp_slot_key"),
            sa.Index("ix_exp_slot_channel", "project_id", "channel"),
        )
    if "exp_evaluation_slots" not in existing:
        op.create_table(
            "exp_evaluation_slots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "evaluation_id",
                sa.Integer(),
                sa.ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "slot_id",
                sa.Integer(),
                sa.ForeignKey("exp_observation_slots.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "evaluation_id", "slot_id", name="uq_exp_evaluation_slot"
            ),
        )


def downgrade() -> None:
    existing = _existing_tables()
    if "exp_evaluation_slots" in existing:
        op.drop_table("exp_evaluation_slots")
    if "exp_observation_slots" in existing:
        op.drop_table("exp_observation_slots")
