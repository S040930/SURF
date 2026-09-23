"""Read-only reconciliation for SAF memory-study artifact directories.

The command reports missing store directories, disk directories that have no
database owner, abandoned staging directories, snapshot/native-artifact hash
mismatches, and total disk use.
It never deletes anything unless ``--purge-orphans`` is supplied explicitly;
    that flag removes orphan study directories and abandoned staging directories.

Run from ``backend/``::

    python scripts/audit_memory_study_artifacts.py --json
    python scripts/audit_memory_study_artifacts.py --study-id <uuid>
    python scripts/audit_memory_study_artifacts.py --purge-orphans --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in {None, ""}:
    # ``python scripts/<name>.py`` puts ``scripts/`` (not ``backend/``) on
    # sys.path.  Keep the documented direct invocation working while leaving
    # normal package execution untouched.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.experiment.memory_study.memory import _sha256_path, memory_hash
from app.models.memory_study import MSMemoryStore, MSStudy


def _size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _safe_top_level(root: Path, value: Path) -> Path | None:
    """Resolve a requested study directory without permitting path escape."""
    try:
        resolved = value.resolve()
    except OSError:
        return None
    if resolved.parent != root or resolved == root:
        return None
    return resolved


def audit(study_id: str | None = None, *, purge_orphans: bool = False) -> dict:
    root = Path(settings.MEMORY_STUDY_ARTIFACT_ROOT).resolve()
    result: dict = {
        "artifact_root": str(root),
        "study_id": study_id,
        "missing_directories": [],
        "orphan_directories": [],
        "temporary_directories": [],
        "unsafe_paths": [],
        "snapshot_hash_mismatches": [],
        "native_artifact_hash_mismatches": [],
        "disk_bytes": _size(root),
        "purged_directories": [],
    }
    with SessionLocal() as db:
        query = select(MSStudy).order_by(MSStudy.id)
        if study_id:
            query = query.where(MSStudy.id == study_id)
        studies = list(db.scalars(query))
        known_studies = {study.id for study in studies}
        for study in studies:
            study_root = root / study.id
            if not study_root.is_dir():
                result["missing_directories"].append(
                    {"study_id": study.id, "path": str(study_root), "kind": "study"}
                )
            stores = db.scalars(
                select(MSMemoryStore).where(MSMemoryStore.study_id == study.id)
            )
            for store in stores:
                if not store.snapshot_path:
                    continue
                snapshot_path = Path(store.snapshot_path)
                allowed_root = (root / study.id / "memory").resolve()
                if not snapshot_path.resolve().is_relative_to(allowed_root):
                    result["unsafe_paths"].append(
                        {
                            "study_id": study.id,
                            "store_id": store.id,
                            "path": str(snapshot_path),
                        }
                    )
                    continue
                if not snapshot_path.is_file() and store.snapshot_json is not None:
                    result["missing_directories"].append(
                        {
                            "study_id": study.id,
                            "store_id": store.id,
                            "path": str(snapshot_path),
                            "kind": "snapshot",
                        }
                    )
                    continue
                if not snapshot_path.is_file():
                    native_dir = snapshot_path.parent / "native-artifacts"
                    if snapshot_path.exists() or native_dir.exists():
                        result["snapshot_hash_mismatches"].append(
                            {
                                "study_id": study.id,
                                "store_id": store.id,
                                "path": str(snapshot_path),
                            }
                        )
                        if native_dir.exists():
                            result["native_artifact_hash_mismatches"].append(
                                {
                                    "study_id": study.id,
                                    "store_id": store.id,
                                    "path": str(native_dir),
                                }
                            )
                    continue
                try:
                    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    valid_json = isinstance(payload, dict)
                except (OSError, ValueError, TypeError):
                    payload, valid_json = None, False
                if (
                    store.snapshot_json is None
                    or not valid_json
                    or store.snapshot_sha256
                    and memory_hash(payload) != store.snapshot_sha256
                    or store.snapshot_json is not None
                    and payload != store.snapshot_json
                ):
                    result["snapshot_hash_mismatches"].append(
                        {"study_id": study.id, "store_id": store.id, "path": str(snapshot_path)}
                    )
                if store.snapshot_json is None:
                    # A not-yet-committed stream may have an empty directory,
                    # but a snapshot/native sidecar is evidence of an
                    # interrupted attempt and cannot be trusted.
                    native_dir = snapshot_path.parent / "native-artifacts"
                    if native_dir.exists():
                        result["native_artifact_hash_mismatches"].append(
                            {
                                "study_id": study.id,
                                "store_id": store.id,
                                "path": str(native_dir),
                            }
                        )
                    continue
                artifact_hash = (
                    payload.get("artifact_sha256") if isinstance(payload, dict) else None
                ) or store.snapshot_artifact_sha256
                if artifact_hash:
                    artifact_dir = snapshot_path.parent / "native-artifacts"
                    if not artifact_dir.is_dir() or _sha256_path(artifact_dir) != artifact_hash:
                        result["native_artifact_hash_mismatches"].append(
                            {"study_id": study.id, "store_id": store.id, "path": str(artifact_dir)}
                        )
        if study_id:
            requested = _safe_top_level(root, root / study_id)
            candidates = [requested] if requested is not None and requested.exists() else []
        else:
            candidates = (
                [path for path in root.iterdir() if path.is_dir()]
                if root.is_dir()
                else []
            )
        for path in candidates:
            if path.name not in known_studies:
                result["orphan_directories"].append(str(path))
                if purge_orphans:
                    safe_path = _safe_top_level(root, path)
                    if safe_path is not None and safe_path.is_dir():
                        shutil.rmtree(safe_path)
                        result["purged_directories"].append(str(safe_path))
        if root.is_dir():
            temporary = [
                path
                for path in root.rglob("*")
                if path.is_dir()
                and not path.is_symlink()
                and (
                    path.name.startswith(("attempt-", "publish-"))
                    # The exact ``native-artifacts`` directory is committed
                    # Mem0 state, not a temporary construction directory.
                    or path.name.startswith("native-")
                    and path.name != "native-artifacts"
                )
            ]
            for path in temporary:
                result["temporary_directories"].append(str(path))
                if purge_orphans:
                    try:
                        safe_path = path.resolve()
                    except OSError:
                        safe_path = None
                    if safe_path is not None and safe_path.is_relative_to(root):
                        shutil.rmtree(safe_path, ignore_errors=True)
                        result["purged_directories"].append(str(safe_path))
    result["disk_bytes_after"] = _size(root)
    result["ok"] = not any(
        result[key]
        for key in (
            "missing_directories",
            "orphan_directories",
            "temporary_directories",
            "unsafe_paths",
            "snapshot_hash_mismatches",
            "native_artifact_hash_mismatches",
        )
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-id")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--purge-orphans",
        action="store_true",
        help="explicitly remove orphan study and abandoned staging directories",
    )
    args = parser.parse_args()
    result = audit(args.study_id, purge_orphans=args.purge_orphans)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"artifact root: {result['artifact_root']}")
        print(f"disk bytes: {result['disk_bytes_after']}")
        for key in (
            "missing_directories",
            "orphan_directories",
            "temporary_directories",
            "unsafe_paths",
            "snapshot_hash_mismatches",
            "native_artifact_hash_mismatches",
            "purged_directories",
        ):
            print(f"{key}: {len(result[key])}")


if __name__ == "__main__":
    main()
