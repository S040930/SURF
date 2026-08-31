"""Rename r20 snapshot word-count columns to token-count columns.

Revision ID: l7b8c9d0e1f2
Revises: k6a7b8c9d0e1
"""

from alembic import op

revision = "l7b8c9d0e1f2"
down_revision = "k6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("r20_snapshots") as batch:
        batch.alter_column("visible_word_count", new_column_name="visible_token_count")
        batch.alter_column("stored_word_count", new_column_name="stored_token_count")


def downgrade() -> None:
    with op.batch_alter_table("r20_snapshots") as batch:
        batch.alter_column("visible_token_count", new_column_name="visible_word_count")
        batch.alter_column("stored_token_count", new_column_name="stored_word_count")
