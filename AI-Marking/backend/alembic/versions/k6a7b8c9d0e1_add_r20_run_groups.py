"""Add r20 run groups (one cell per question x condition).

Revision ID: k6a7b8c9d0e1
Revises: 72159e6e2de6
"""

from typing import Sequence, Union

from alembic import op

revision: str = "k6a7b8c9d0e1"
down_revision: Union[str, None] = "72159e6e2de6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "r20_run_groups"


def upgrade() -> None:
    from app.db.base import Base

    Base.metadata.tables[TABLE].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    Base.metadata.tables[TABLE].drop(bind=op.get_bind(), checkfirst=True)
