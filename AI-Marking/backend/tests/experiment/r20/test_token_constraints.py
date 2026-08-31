from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.experiment.r20.calibration import calibration_from_archive
from app.experiment.r20.memory import (
    parse_memory_update,
    stored_token_count,
    visible_token_count,
)
from app.experiment.r20.prompts import (
    NORMATIVE_FIELD_SHA256,
    NORMATIVE_TEMPLATES_SHA256,
    templates_hash,
)
from app.experiment.r20.protocol import (
    ANSWER_COPY_TOKENS,
    MAX_CORE_TOKENS,
    MAX_FEEDBACK_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
    PromptTemplates,
    ScoreOutput,
)
from app.experiment.r20.tokenization import TOKENIZER_NAME, token_count, token_ids

WORKSPACE = Path(__file__).resolve().parents[5]
AI_ROOT = Path(__file__).resolve().parents[4]
DOCS = AI_ROOT / "docs" / "experiment"


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


def test_tokenizer_is_fixed_and_treats_special_text_as_literal():
    assert TOKENIZER_NAME == "o200k_base"
    assert token_count("") == 0
    assert token_count("Hello, world!") == 4
    assert token_count("你好，世界！") > 0
    assert token_count("<|endoftext|>") == len(token_ids("<|endoftext|>"))


def test_field_token_limits_accept_boundary_and_reject_one_more():
    prefix = "If a future answer explains photosynthesis "
    condition = prefix
    while token_count(condition) < MAX_CORE_TOKENS:
        condition += " x"
    assert token_count(condition) == MAX_CORE_TOKENS
    valid = _crm(
        condition,
        _text_with_tokens(MAX_GUIDANCE_TOKENS - token_count("supports")),
        _text_with_tokens(MAX_SUPPORT_TOKENS),
    )
    parse_memory_update(
        "crm", valid, "unrelated answer", "Explain photosynthesis", "Plants use light"
    )

    invalid = _crm(f"{condition} x", "short", "short")
    with pytest.raises(ValueError, match="condition exceeds"):
        parse_memory_update(
            "crm",
            invalid,
            "unrelated answer",
            "Explain photosynthesis",
            "Plants use light",
        )


def test_snapshot_budget_counts_content_fields_not_json_structure():
    item = {
        "condition": _text_with_tokens(MAX_CORE_TOKENS),
        "effect": "supports",
        "importance": "major",
        "guidance": _text_with_tokens(MAX_GUIDANCE_TOKENS - 1),
        "support": _text_with_tokens(MAX_SUPPORT_TOKENS),
    }
    assert stored_token_count([item]) == 103
    assert stored_token_count([item]) < MAX_STORED_TOKENS
    assert visible_token_count("crm", [item]) == 73
    assert visible_token_count("crm", [item]) < MAX_VISIBLE_TOKENS


def test_feedback_limit_uses_tokens():
    ScoreOutput(score=1, feedback=_text_with_tokens(MAX_FEEDBACK_TOKENS))
    with pytest.raises(ValidationError, match="feedback exceeds"):
        ScoreOutput(score=1, feedback=_text_with_tokens(MAX_FEEDBACK_TOKENS + 1))


def test_answer_copy_uses_frozen_token_sequence():
    answer = _text_with_tokens(ANSWER_COPY_TOKENS + 5)
    copied = _text_with_tokens(ANSWER_COPY_TOKENS)
    # Build the copied substring from the answer's token prefix to avoid relying
    # on word boundaries in the BPE vocabulary.
    source = token_ids(answer)[:ANSWER_COPY_TOKENS]
    from app.experiment.r20.tokenization import tokenizer

    copied = tokenizer().decode(list(source))
    with pytest.raises(ValueError, match="answer copy"):
        parse_memory_update(
            "crm",
            _crm(
                "If a future answer explains photosynthesis",
                "photosynthesis is supported",
                copied,
            ),
            answer,
            "Explain photosynthesis",
            "Plants use light",
        )


def test_calibration_artifact_is_reproducible():
    result = calibration_from_archive(
        WORKSPACE / "data" / "SAF2_0.zip", DOCS / "r20-prompt-design.md"
    )
    artifact = json.loads((DOCS / "r20-token-calibration.json").read_text())
    assert result == artifact
    assert artifact["limits"] == {
        "answer_copy": ANSWER_COPY_TOKENS,
        "core": MAX_CORE_TOKENS,
        "feedback": MAX_FEEDBACK_TOKENS,
        "guidance": MAX_GUIDANCE_TOKENS,
        "stored_snapshot": MAX_STORED_TOKENS,
        "support": MAX_SUPPORT_TOKENS,
        "visible_snapshot": MAX_VISIBLE_TOKENS,
    }


def test_prompt_design_has_token_limits_and_no_legacy_word_budgets():
    text = (DOCS / "r20-prompt-design.md").read_text()
    for value in (30, 42, 118, 441, 618):
        assert f"{value} token" in text
    assert "18-token" in text
    assert "80 English words" not in text
    assert "300 words" not in text
    assert "420 words" not in text


def test_normative_document_blocks_match_frozen_template_hash():
    text = (DOCS / "r20-prompt-design.md").read_text()
    blocks = re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    assert len(blocks) == 3
    templates = PromptTemplates(
        scoring=blocks[0], crm_update=blocks[1], arm_update=blocks[2]
    )
    assert templates_hash(templates) == NORMATIVE_TEMPLATES_SHA256
    assert {
        name: hashlib.sha256(value.encode()).hexdigest()
        for name, value in templates.model_dump().items()
    } == NORMATIVE_FIELD_SHA256
    assert '"effect":"supports|weakens"' in blocks[1]
    assert '"anchors":{"sufficient":"...","partial":"...","missing":"..."}' in blocks[2]
    assert "fixed points, automatic decisions" in blocks[0]
    assert "condition must be atomic" in blocks[1]
    assert "criterion must not contain if, when, whenever" in blocks[2]


def test_active_documents_do_not_reference_v1_contract():
    active = [
        AI_ROOT / "README.md",
        AI_ROOT / "PROJECT.md",
        DOCS / "r20-prompt-design.md",
        DOCS / "r20-implementation.md",
        WORKSPACE / "PROJECT.md",
        WORKSPACE / "docs" / "research-design-r20.md",
    ]
    for path in active:
        assert "r20-saf-official-split-2026-08-v1" not in path.read_text(), path
