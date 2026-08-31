"""Remove the retired r19 experiment tables."""

from typing import Sequence, Union

from alembic import op

revision: str = "j5d6e7f8a9b0"
down_revision: Union[str, None] = "i4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "r19_call_attempts",
    "r19_calls",
    "r19_snapshots",
    "r19_streams",
    "r19_orders",
    "r19_records",
    "r19_question_reviews",
    "r19_analysis_results",
    "r19_workers",
    "r19_projects",
    "r19_prompt_trials",
    "r19_prompt_versions",
    "r19_model_configs",
)


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect

    inspector = inspect(bind)
    for table in TABLES:
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError("r19 platform removal is irreversible")
