from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.experiment.r20.tokenization import token_count
from app.experiment.r21.memory import parse_memory_update
from app.experiment.r21.protocol import (
    MAX_CORE_TOKENS,
    MAX_FEEDBACK_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
    ScoreOutput,
)


def _text_with_tokens(count: int) -> str:
    value = " ".join("x" for _ in range(count))
    assert token_count(value) == count
    return value


def _crm(condition: str, guidance: str, support: str) -> dict:
    return {
        "rules": [
            {
                "condition": condition,
                "effect": "supports",
                "importance": "major",
                "guidance": guidance,
                "support": support,
            }
        ]
    }


def test_r21_token_calibration_is_relaxed_and_independent_from_r20():
    assert {
        "feedback": MAX_FEEDBACK_TOKENS,
        "core": MAX_CORE_TOKENS,
        "guidance": MAX_GUIDANCE_TOKENS,
        "support": MAX_SUPPORT_TOKENS,
        "visible": MAX_VISIBLE_TOKENS,
        "stored": MAX_STORED_TOKENS,
    } == {
        "feedback": 180,
        "core": 48,
        "guidance": 72,
        "support": 48,
        "visible": 720,
        "stored": 960,
    }


def test_r21_accepts_relaxed_boundaries_and_rejects_one_extra_token():
    condition = "If a future answer explains photosynthesis"
    while token_count(condition) < MAX_CORE_TOKENS:
        condition += " x"
    valid = _crm(
        condition,
        _text_with_tokens(MAX_GUIDANCE_TOKENS - token_count("supports")),
        _text_with_tokens(MAX_SUPPORT_TOKENS),
    )
    parse_memory_update(
        "crm", valid, "unrelated answer", "Explain photosynthesis", "Plants use light"
    )

    with pytest.raises(ValueError, match="condition exceeds"):
        parse_memory_update(
            "crm",
            _crm(f"{condition} x", "short", "short"),
            "unrelated answer",
            "Explain photosynthesis",
            "Plants use light",
        )


def test_r21_feedback_limit_uses_relaxed_token_budget():
    ScoreOutput(score=1, feedback=_text_with_tokens(MAX_FEEDBACK_TOKENS))
    with pytest.raises(ValidationError, match="feedback exceeds"):
        ScoreOutput(score=1, feedback=_text_with_tokens(MAX_FEEDBACK_TOKENS + 1))
