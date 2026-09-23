"""Add site-level runner configs and embedding site config.

Revision ID: y1z2a3b4c5d6
Revises: x0y1z2a3b4c5
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "y1z2a3b4c5d6"
down_revision: Union[str, None] = "x0y1z2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.config import settings
    from app.core.time import utc_now_naive
    from app.db.base import Base
    from app.experiment.memory_study.protocol import EMBEDDING_MODEL

    bind = op.get_bind()
    Base.metadata.tables["ms_runner_configs"].create(bind, checkfirst=True)
    Base.metadata.tables["ms_site_config"].create(bind, checkfirst=True)

    site_config = sa.table(
        "ms_site_config",
        sa.column("id", sa.Integer()),
        sa.column("embedding_model", sa.String(length=200)),
        sa.column("embedding_revision", sa.String(length=80)),
        sa.column("embedding_backend", sa.String(length=24)),
        sa.column("embedding_api_base", sa.String(length=400)),
        sa.column("embedding_api_key", sa.String(length=400)),
        sa.column("embedding_dims", sa.Integer()),
        sa.column("updated_at", sa.DateTime()),
    )
    if bind.execute(sa.select(site_config.c.id).limit(1)).first() is None:
        op.bulk_insert(
            site_config,
            [
                {
                    "id": 1,
                    "embedding_model": (
                        settings.MEMORY_STUDY_EMBEDDING_MODEL or EMBEDDING_MODEL
                    ),
                    "embedding_revision": settings.MEMORY_STUDY_EMBEDDING_REVISION,
                    "embedding_backend": "openai",
                    "embedding_api_base": "",
                    "embedding_api_key": "",
                    "embedding_dims": None,
                    "updated_at": utc_now_naive(),
                }
            ],
        )


def downgrade() -> None:
    from app.db.base import Base

    bind = op.get_bind()
    Base.metadata.tables["ms_site_config"].drop(bind, checkfirst=True)
    Base.metadata.tables["ms_runner_configs"].drop(bind, checkfirst=True)
