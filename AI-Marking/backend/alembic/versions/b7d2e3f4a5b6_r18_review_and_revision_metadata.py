"""Add r18 question review and immutable prompt revision metadata.

Revision ID: b7d2e3f4a5b6
Revises: a6c1e13887ee
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d2e3f4a5b6"
down_revision: Union[str, None] = "a6c1e13887ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "r18_prompt_versions",
        sa.Column("parent_version_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_r18_prompt_parent",
        "r18_prompt_versions",
        "r18_prompt_versions",
        ["parent_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_r18_prompt_parent", "r18_prompt_versions", ["parent_version_id"]
    )
    op.add_column(
        "r18_projects",
        sa.Column("prompt_version_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "r18_projects",
        sa.Column("prompt_version_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("r18_projects", "prompt_version_deleted", server_default=None)
    op.add_column(
        "r18_prompt_trials",
        sa.Column(
            "model_config_id", sa.String(length=36), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "r18_prompt_trials",
        sa.Column(
            "model_config_sha256",
            sa.String(length=64),
            nullable=False,
            server_default="0" * 64,
        ),
    )
    op.alter_column("r18_prompt_trials", "model_config_id", server_default=None)
    op.alter_column("r18_prompt_trials", "model_config_sha256", server_default=None)
    op.create_table(
        "r18_question_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("r18_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=128), nullable=False),
        sa.Column("question_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "project_id", "question_id", name="uq_r18_question_review"
        ),
    )
    op.create_index(
        "ix_r18_question_review_project",
        "r18_question_reviews",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_r18_question_review_project", table_name="r18_question_reviews")
    op.drop_table("r18_question_reviews")
    op.drop_column("r18_projects", "prompt_version_name")
    op.drop_column("r18_projects", "prompt_version_deleted")
    op.drop_column("r18_prompt_trials", "model_config_sha256")
    op.drop_column("r18_prompt_trials", "model_config_id")
    op.drop_index("ix_r18_prompt_parent", table_name="r18_prompt_versions")
    op.drop_constraint(
        "fk_r18_prompt_parent", "r18_prompt_versions", type_="foreignkey"
    )
    op.drop_column("r18_prompt_versions", "parent_version_id")
