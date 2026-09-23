"""Add isolated SAF 2.0 memory-framework study tables.

Revision ID: v8w9x0y1z2
Revises: u7v8w9x0y1z2
"""

from typing import Sequence, Union

from alembic import op


revision: str = "v8w9x0y1z2"
down_revision: Union[str, None] = "u7v8w9x0y1z2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "ms_studies",
    "ms_records",
    "ms_memory_stores",
    "ms_calls",
    "ms_call_attempts",
    "ms_audit_events",
    "ms_reports",
    "ms_scheduler_runtime",
)


def upgrade() -> None:
    """Create only the new ``ms_`` namespace; legacy tables are untouched."""
    from app.db.base import Base

    bind = op.get_bind()
    for table in TABLES:
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(bind=bind, checkfirst=True)
