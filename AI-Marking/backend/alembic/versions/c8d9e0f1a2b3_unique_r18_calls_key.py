"""Enforce one logical key per r18 call row.

Earlier recover/retry loops could create duplicate rows for the same logical
call, and unordered ``limit(1)`` lookups let stream barriers resolve to
different copies — stalling a project at "250 calls done" until it was
deduplicated by hand.  The unique constraint makes the invariant permanent.

Revision ID: c8d9e0f1a2b3
Revises: d9f4a5b6c7d8
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "d9f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_r18_call_key",
        "r18_calls",
        [
            "project_id",
            "question_id",
            "condition",
            "path",
            "kind",
            "entry_id",
            "history_count",
        ],
    )


def downgrade() -> None:
    op.drop_constraint("uq_r18_call_key", "r18_calls", type_="unique")
