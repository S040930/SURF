"""Add ms_scheduler_runtime worker pid/host for dead-lease takeover."""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("ms_scheduler_runtime")
    }
    if "worker_pid" not in columns:
        op.add_column(
            "ms_scheduler_runtime", sa.Column("worker_pid", sa.Integer(), nullable=True)
        )
    if "worker_host" not in columns:
        op.add_column(
            "ms_scheduler_runtime",
            sa.Column("worker_host", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("ms_scheduler_runtime")
    }
    if "worker_host" in columns:
        op.drop_column("ms_scheduler_runtime", "worker_host")
    if "worker_pid" in columns:
        op.drop_column("ms_scheduler_runtime", "worker_pid")
