"""Allow repeatable r23 runs with one runner configuration.

Revision ID: r3h4i5j6k7l8
Revises: q2g3h4i5j6k7
"""

from typing import Sequence, Union

from sqlalchemy import inspect

from alembic import op

revision: str = "r3h4i5j6k7l8"
down_revision: Union[str, None] = "q2g3h4i5j6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    constraints = {
        item["name"] for item in inspector.get_unique_constraints("r23_projects")
    }
    if "uq_r23_formal_signature" in constraints:
        op.drop_constraint("uq_r23_formal_signature", "r23_projects", type_="unique")
    indexes = {item["name"] for item in inspector.get_indexes("r23_projects")}
    if "ix_r23_formal_signature" not in indexes:
        op.create_index(
            "ix_r23_formal_signature",
            "r23_projects",
            ["formal_signature"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_r23_formal_signature", table_name="r23_projects")
    op.create_unique_constraint(
        "uq_r23_formal_signature", "r23_projects", ["formal_signature"]
    )
