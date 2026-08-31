"""Add the singleton r21 MCP runner lease.

Revision ID: o0e1f2a3b4c5
Revises: n9d0e1f2a3b4
"""

from typing import Sequence, Union

from alembic import op

revision: str = "o0e1f2a3b4c5"
down_revision: Union[str, None] = "n9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base import Base

    Base.metadata.tables["r21_runner_runtime"].create(
        bind=op.get_bind(), checkfirst=True
    )


def downgrade() -> None:
    from app.db.base import Base

    Base.metadata.tables["r21_runner_runtime"].drop(bind=op.get_bind(), checkfirst=True)
