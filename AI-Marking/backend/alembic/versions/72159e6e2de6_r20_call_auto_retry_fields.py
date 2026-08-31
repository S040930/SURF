"""r20 call auto retry fields

Revision ID: 72159e6e2de6
Revises: j5d6e7f8a9b0
Create Date: 2026-08-13 13:07:32.287449

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '72159e6e2de6'
down_revision: Union[str, None] = 'j5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "r20_calls",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("r20_calls", sa.Column("next_retry_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("r20_calls", "next_retry_at")
    op.drop_column("r20_calls", "retry_count")
