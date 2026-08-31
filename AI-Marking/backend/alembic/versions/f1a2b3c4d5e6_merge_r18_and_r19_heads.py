"""Merge the historical audit branch before the active r20 protocol.

Revision ID: f1a2b3c4d5e6
Revises: c8d9e0f1a2b3, e0f1a2b3c4d5
"""

from typing import Sequence, Union


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, tuple[str, str], None] = (
    "c8d9e0f1a2b3",
    "e0f1a2b3c4d5",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The two parent revisions own all schema changes."""


def downgrade() -> None:
    """The two parent revisions own all schema changes."""
