"""Dependency-free runner for the manuscript-checker regression tests.

The tests themselves remain pytest-compatible.  This runner makes the
offline revision package reproducible on the project's macOS Python where
pytest is not installed, without downloading a new dependency.
"""

from __future__ import annotations

import importlib.util
import json
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEST_FILE = ROOT / "AI-Marking" / "backend" / "tests" / "test_paper_draft_check.py"


def main() -> None:
    spec = importlib.util.spec_from_file_location("paper_draft_tests", TEST_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TEST_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for name in sorted(item for item in dir(module) if item.startswith("test_")):
        func = getattr(module, name)
        try:
            func()
            results.append({"name": name, "status": "pass"})
        except Exception as exc:  # report all failures rather than hiding later cases
            results.append({"name": name, "status": "fail", "error": str(exc), "traceback": traceback.format_exc()})
    report = {
        "runner": "dependency-free direct invocation of pytest-compatible functions",
        "pytest_available": False,
        "tests": results,
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": sum(item["status"] == "fail" for item in results),
    }
    output = ROOT / "outputs" / "exp_dress_new_paper_revision_0914" / "tests" / "checker_regression_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
