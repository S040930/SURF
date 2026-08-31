"""Add isolated r21 Codex CLI platform tables.

Revision ID: n9d0e1f2a3b4
Revises: m8c9d0e1f2a3
"""

from typing import Sequence, Union

from alembic import op

revision: str = "n9d0e1f2a3b4"
down_revision: Union[str, None] = "m8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "r21_runner_configs", "r21_prompt_versions", "r21_projects", "r21_records",
    "r21_streams", "r21_run_groups", "r21_snapshots", "r21_calls",
    "r21_call_attempts", "r21_reports", "r21_exposures",
)


def upgrade() -> None:
    # Models are imported by app.db.base, keeping this append-only migration and
    # its SQL definition exactly aligned with the domain schema.
    from app.db.base import Base

    bind = op.get_bind()
    for table in TABLES:
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(bind=bind, checkfirst=True)
