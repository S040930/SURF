"""Add ms_calls.retry_after for deferred exponential-backoff retries."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "b3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("ms_calls")
    }
    if "retry_after" not in columns:
        op.add_column(
            "ms_calls", sa.Column("retry_after", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("ms_calls")
    }
    if "retry_after" in columns:
        op.drop_column("ms_calls", "retry_after")
