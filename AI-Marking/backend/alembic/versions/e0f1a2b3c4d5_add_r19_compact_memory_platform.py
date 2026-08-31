"""Retired r19 compact external-memory experiment migration.

Revision ID: e0f1a2b3c4d5
Revises: d9f4a5b6c7d8
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "r19_prompt_versions",
    "r19_prompt_trials",
    "r19_projects",
    "r19_question_reviews",
    "r19_records",
    "r19_orders",
    "r19_streams",
    "r19_snapshots",
    "r19_calls",
    "r19_call_attempts",
    "r19_analysis_results",
    "r19_workers",
)


def upgrade() -> None:
    # r19 was removed; this historical revision intentionally does nothing.
    pass


def downgrade() -> None:
    pass
