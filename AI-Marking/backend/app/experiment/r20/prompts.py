"""Frozen r20 hash and scoring-safe memory serialization."""

from __future__ import annotations

import hashlib
import json

from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.memory import (
    render_memory,
    stored_token_count,
    visible_token_count,
)
from app.experiment.r20.protocol import PromptTemplates

SERIALIZATION_VERSION = "r20-json-v2"
NORMATIVE_TEMPLATES_SHA256 = (
    "615695bcf9d46fbdb2efb92b090e06f0ff11031b39185db26cf7939eb7e56663"
)
NORMATIVE_FIELD_SHA256 = {
    "scoring": "a06e73ee38e04ec558c221c06b2801b414cd621296039d1499ae8b3dcc58d617",
    "crm_update": "190446fdc4a368959f2b9413187559b0d605cc5655df7c8a18fc6e94480cee58",
    "arm_update": "bce95f2e18413f95cda91e662171e9443110fd0440ad6f00f76eb16a4166ca3b",
}


def normalize_copied_template(text: str) -> str:
    """Remove clipboard-only line-boundary differences, not prompt content."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def normalize_copied_templates(templates: PromptTemplates) -> PromptTemplates:
    return PromptTemplates(
        **{
            name: normalize_copied_template(value)
            for name, value in templates.model_dump().items()
        }
    )


def mismatched_normative_fields(templates: PromptTemplates) -> list[str]:
    return [
        name
        for name, value in templates.model_dump().items()
        if hashlib.sha256(value.encode()).hexdigest() != NORMATIVE_FIELD_SHA256[name]
    ]


def templates_hash(templates: PromptTemplates) -> str:
    payload = {
        "protocol": PROTOCOL_ID,
        "serialization": SERIALIZATION_VERSION,
        "templates": templates.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


__all__ = [
    "render_memory",
    "mismatched_normative_fields",
    "NORMATIVE_FIELD_SHA256",
    "NORMATIVE_TEMPLATES_SHA256",
    "normalize_copied_templates",
    "stored_token_count",
    "templates_hash",
    "visible_token_count",
]
