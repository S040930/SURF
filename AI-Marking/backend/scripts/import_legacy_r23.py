"""Run the read-only legacy r23 import (idempotent).

Usage (from backend/):  .venv/bin/python -m scripts.import_legacy_r23
"""

from __future__ import annotations

import json

from app.db.session import SessionLocal
from app.experiment.core.legacy_import import import_legacy_r23


def main() -> None:
    with SessionLocal() as db:
        summary = import_legacy_r23(db)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
