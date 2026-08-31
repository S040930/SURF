"""Remove the retired pre-r20 platform.

Revision ID: g2a3b4c5d6e7
Revises: f1a2b3c4d5e6
"""

from typing import Sequence, Union

from alembic import op


revision: str = "g2a3b4c5d6e7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in (
        "r18_recovery_events",
        "r18_question_reviews",
        "r18_call_attempts",
        "r18_analysis_results",
        "r18_calls",
        "r18_snapshots",
        "r18_streams",
        "r18_orders",
        "r18_records",
        "r18_projects",
        "r18_prompt_trials",
        "r18_prompt_versions",
        "r18_workers",
        "r18_model_configs",
    ):
        op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError("removing the retired experiment platform is irreversible")
