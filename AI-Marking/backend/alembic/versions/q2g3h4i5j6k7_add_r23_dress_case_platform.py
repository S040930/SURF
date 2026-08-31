"""Add isolated r23 DREsS_CASE experiment tables.

Revision ID: q2g3h4i5j6k7
Revises: p1f2a3b4c5d6
"""

from typing import Sequence, Union

from alembic import op

revision: str = "q2g3h4i5j6k7"
down_revision: Union[str, None] = "p1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "r23_runner_configs",
    "r23_rubric_versions",
    "r23_projects",
    "r23_model_bindings",
    "r23_sample_observations",
    "r23_run_groups",
    "r23_unique_evaluations",
    "r23_evaluation_observations",
    "r23_call_attempts",
    "r23_events",
    "r23_locked_reports",
)


def upgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in TABLES:
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(bind=bind, checkfirst=True)
