#!/usr/bin/env python
"""Verify the frozen V3/V3-r2 scoring instruments without making a model call."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.experiment.common.codex_runner import (  # noqa: E402
    PROMPT_ENVELOPE_VERSION,
)
from app.experiment.memory_study import (  # noqa: E402
    PROTOCOL_ID,
    SUPPORTED_PROTOCOL_IDS,
    V3_R2_PROTOCOL_ID,
)
from app.experiment.memory_study.protocol import (  # noqa: E402
    scoring_context_for_protocol,
)
from app.experiment.memory_study.worker import _score_messages  # noqa: E402

EXPECTED_INSTRUMENT_SHA256 = {
    PROTOCOL_ID: "7d6390e9bf15006eb81376a6561c4b701693096455370b65d28c15a121fd93bc",
    V3_R2_PROTOCOL_ID: "506e86bbc839092fa7240d0f6023aa052122702ac22f49b5efd6a6d6b8f397cc",
}


def _runtime_instrument(protocol_id: str) -> tuple[str, list[str], dict]:
    record = {
        "protocol_id": protocol_id,
        "question_text": "question",
        "reference_answer": "reference",
        "student_answer": "answer",
        "max_score": 4.0,
        "score_floor": 0.0,
        "score_ceiling": 4.0,
    }
    messages = _score_messages(
        SimpleNamespace(condition="retrieval_full"), record, []
    )
    return messages[0]["content"], list(json.loads(messages[1]["content"])), json.loads(
        messages[1]["content"]
    )


def _check_protocol(protocol_id: str) -> list[dict[str, object]]:
    context = scoring_context_for_protocol(protocol_id)
    system, keys, payload = _runtime_instrument(protocol_id)
    declared_keys = list(context["payload_fields"])
    expected_system = str(context["system_prompt"])
    checks = [
        {
            "name": f"{protocol_id}: system prompt",
            "expected": expected_system,
            "found": system,
            "match": system == expected_system,
        },
        {
            "name": f"{protocol_id}: payload fields",
            "expected": sorted(declared_keys),
            "found": sorted(keys),
            "match": sorted(declared_keys) == sorted(keys),
        },
        {
            "name": f"{protocol_id}: payload visibility",
            "expected": declared_keys,
            "found": list(payload),
            "match": set(payload) == set(declared_keys),
        },
        {
            "name": f"{protocol_id}: instrument fingerprint",
            "expected": EXPECTED_INSTRUMENT_SHA256[protocol_id],
            "found": context["instrument_sha256"],
            "match": context["instrument_sha256"]
            == EXPECTED_INSTRUMENT_SHA256[protocol_id],
        },
    ]
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    expected_protocols = {PROTOCOL_ID, V3_R2_PROTOCOL_ID}
    checks: list[dict[str, object]] = [
        {
            "name": "supported protocols",
            "expected": sorted(expected_protocols),
            "found": sorted(SUPPORTED_PROTOCOL_IDS),
            "match": set(SUPPORTED_PROTOCOL_IDS) == expected_protocols,
        },
        {
            "name": "prompt envelope",
            "expected": "saf-memory-study-codex-exec-v2",
            "found": PROMPT_ENVELOPE_VERSION,
            "match": PROMPT_ENVELOPE_VERSION == "saf-memory-study-codex-exec-v2",
        },
    ]
    checks.extend(
        check
        for protocol_id in (PROTOCOL_ID, V3_R2_PROTOCOL_ID)
        for check in _check_protocol(protocol_id)
    )
    ok = all(bool(check["match"]) for check in checks)

    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            mark = "OK  " if check["match"] else "DIFF"
            print(f"[{mark}] {check['name']}")
            if not check["match"]:
                print(f"       expected: {check['expected']}")
                print(f"       found:    {check['found']}")
        print("scoring instruments match" if ok else "SCORING INSTRUMENT CHANGED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
