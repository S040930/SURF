"""Seed the unified experiment platform with the DREsS_New dataset revision
and the original DREsS rubric (Yoo et al., ACL 2025, Table 2 — verbatim).

Idempotent: re-running re-verifies the pinned file hash, reuses the existing
revision row when the dataset is unchanged, and creates the rubric only when
no rubric with the same sha256 exists for the template.

Usage (from backend/):  .venv/bin/python -m scripts.seed_dress_new_rubric
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.experiment.templates.dress_new import (
    DATASET_KEY,
    ORIGINAL_RUBRIC_TEXT,
    TEMPLATE_ID,
)
from app.models.experiments import ExpRubricVersion
from app.services.experiments import ExperimentService, canonical_sha256

RUBRIC_NAME = "DREsS 原始三维 rubric（Yoo et al., ACL 2025, Table 2 逐字转写）"


def seed(*, datasets_root: str | None = None) -> dict[str, str]:
    with SessionLocal() as db:
        service = ExperimentService(db)
        if datasets_root:
            service.datasets_root = Path(datasets_root)
        _, revision, _ = service.ensure_dataset_revision(DATASET_KEY)

        rubric_sha256 = canonical_sha256(
            {"template_id": TEMPLATE_ID, "rubric": ORIGINAL_RUBRIC_TEXT}
        )
        existing = db.scalar(
            select(ExpRubricVersion).where(
                ExpRubricVersion.template_id == TEMPLATE_ID,
                ExpRubricVersion.rubric_sha256 == rubric_sha256,
            )
        )
        if existing is not None:
            created = False
            rubric_id = existing.id
        else:
            row = ExpRubricVersion(
                id=str(uuid.uuid4()),
                template_id=TEMPLATE_ID,
                name=RUBRIC_NAME,
                rubric_text=ORIGINAL_RUBRIC_TEXT,
                rubric_sha256=rubric_sha256,
                status="ready",
            )
            db.add(row)
            db.commit()
            created = True
            rubric_id = row.id
        return {
            "dataset_revision_id": revision.id,
            "revision_label": revision.revision_label,
            "rubric_id": str(rubric_id),
            "rubric_sha256": rubric_sha256,
            "created": str(created),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets-root",
        default=None,
        help="Override the restricted dataset root (defaults to settings).",
    )
    args = parser.parse_args()
    result = seed(datasets_root=args.datasets_root)
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
