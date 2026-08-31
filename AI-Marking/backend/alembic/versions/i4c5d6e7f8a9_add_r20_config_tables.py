"""Add r20-owned model and prompt configuration tables.

Revision ID: i4c5d6e7f8a9
Revises: h3b4c5d6e7f8
"""

from typing import Sequence, Union

from alembic import op

revision: str = "i4c5d6e7f8a9"
down_revision: Union[str, None] = "h3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("r20_model_configs", "r20_prompt_versions", "r20_prompt_trials")


def upgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in TABLES:
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(bind=op.get_bind(), checkfirst=True)
