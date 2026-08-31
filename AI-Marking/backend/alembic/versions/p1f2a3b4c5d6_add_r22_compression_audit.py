"""Add r22 overflow-recovery audit records.

Revision ID: p1f2a3b4c5d6
Revises: o0e1f2a3b4c5
"""

from typing import Sequence, Union

from alembic import op

revision: str = "p1f2a3b4c5d6"
down_revision: Union[str, None] = "o0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in ("r22_calls", "r22_compression_audits"):
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in ("r22_compression_audits", "r22_calls"):
        Base.metadata.tables[table].drop(bind=bind, checkfirst=True)
