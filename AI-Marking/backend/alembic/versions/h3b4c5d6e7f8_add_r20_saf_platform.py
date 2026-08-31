"""Add isolated r20 SAF official-split experiment tables.

Revision ID: h3b4c5d6e7f8
Revises: g2a3b4c5d6e7
"""

from typing import Sequence, Union

from alembic import op

revision: str = "h3b4c5d6e7f8"
down_revision: Union[str, None] = "g2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "r20_projects", "r20_records", "r20_streams", "r20_snapshots",
    "r20_calls", "r20_call_attempts", "r20_reports", "r20_exposures",
)


def upgrade() -> None:
    from app.db.base import Base

    for table in TABLES:
        Base.metadata.tables[table].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(bind=op.get_bind(), checkfirst=True)
