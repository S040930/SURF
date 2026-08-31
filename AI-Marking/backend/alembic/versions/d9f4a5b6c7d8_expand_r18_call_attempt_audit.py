"""Expand r18 call attempt audit metadata.

Revision ID: d9f4a5b6c7d8
Revises: c8e3f4a5b6c7
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d9f4a5b6c7d8"
down_revision: Union[str, None] = "c8e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "r18_call_attempts",
        sa.Column(
            "request_sha256",
            sa.String(length=64),
            nullable=False,
            server_default="0" * 64,
        ),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column(
            "requested_model",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("returned_model", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("system_fingerprint", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "r18_call_attempts",
        sa.Column("refusal", sa.Text(), nullable=True),
    )

    op.execute(
        "UPDATE r18_call_attempts "
        "SET requested_model = COALESCE(model_identity, ''), "
        "returned_model = model_identity"
    )
    op.alter_column("r18_call_attempts", "request_sha256", server_default=None)
    op.alter_column("r18_call_attempts", "status", server_default=None)
    op.alter_column("r18_call_attempts", "requested_model", server_default=None)
    op.alter_column("r18_call_attempts", "started_at", server_default=None)
    op.create_unique_constraint(
        "uq_r18_call_attempt",
        "r18_call_attempts",
        ["call_id", "attempt_number"],
    )
    op.drop_column("r18_call_attempts", "model_identity")


def downgrade() -> None:
    op.add_column(
        "r18_call_attempts",
        sa.Column("model_identity", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE r18_call_attempts "
        "SET model_identity = COALESCE(returned_model, requested_model)"
    )
    op.drop_constraint("uq_r18_call_attempt", "r18_call_attempts", type_="unique")
    for column in (
        "refusal",
        "finish_reason",
        "started_at",
        "system_fingerprint",
        "provider_request_id",
        "returned_model",
        "requested_model",
        "status",
        "request_sha256",
    ):
        op.drop_column("r18_call_attempts", column)
