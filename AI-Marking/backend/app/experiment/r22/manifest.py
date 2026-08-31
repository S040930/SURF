"""Frozen manifest helpers for r22 recovery settings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.experiment.r22 import COMPRESSION_VERSION, PROTOCOL_ID
from app.experiment.r22.protocol import (
    MAX_CORE_TOKENS,
    MAX_FEEDBACK_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
    PROMPT_ENVELOPE_VERSION,
)


def compression_manifest(*, prompt_sha256: str, runtime: dict[str, Any]) -> dict[str, Any]:
    """Build the exact strategy payload stored in a frozen r22 project."""
    return {
        "protocol": PROTOCOL_ID,
        "compression": {
            "version": COMPRESSION_VERSION,
            "max_calls_per_logical_call": 1,
            "failure_policy": "failed_terminal",
            "protected_fields": "structure_and_discrete_values",
            "prompt_sha256": prompt_sha256,
        },
        "tokenizer": {"name": "o200k_base", "implementation": "r20.tokenization"},
        "limits": {
            "feedback": MAX_FEEDBACK_TOKENS,
            "core": MAX_CORE_TOKENS,
            "guidance": MAX_GUIDANCE_TOKENS,
            "support": MAX_SUPPORT_TOKENS,
            "visible": MAX_VISIBLE_TOKENS,
            "stored": MAX_STORED_TOKENS,
        },
        "runtime": dict(runtime),
        "prompt_envelope_version": PROMPT_ENVELOPE_VERSION,
    }


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = ["compression_manifest", "manifest_sha256"]
