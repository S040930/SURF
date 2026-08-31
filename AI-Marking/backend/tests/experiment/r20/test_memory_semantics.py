from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.experiment.r20.memory import parse_memory_update, render_memory

QUESTION = "Explain how photosynthesis uses light energy and carbon dioxide."
REFERENCE = "Plants use light energy and carbon dioxide to make glucose and oxygen."
AI_ROOT = Path(__file__).resolve().parents[4]


def _crm(**changes):
    rule = {
        "condition": "If a future answer explains light energy in photosynthesis",
        "effect": "supports",
        "importance": "major",
        "guidance": "Light energy addresses the photosynthesis mechanism.",
        "support": "Teacher feedback repeatedly emphasized the energy mechanism.",
    }
    rule.update(changes)
    return {"rules": [rule]}


def _arm(**changes):
    item = {
        "criterion": "Photosynthesis energy conversion",
        "importance": "major",
        "anchors": {
            "sufficient": "Light energy makes glucose from carbon dioxide.",
            "partial": "Light energy is present but glucose is missing.",
            "missing": "Light energy or carbon dioxide is absent.",
        },
        "support": "Teacher feedback repeatedly emphasized this mechanism.",
    }
    item.update(changes)
    return {"rubric": [item]}


def _parse(condition: str, value: dict):
    return parse_memory_update(
        condition, value, "A short unrelated response.", QUESTION, REFERENCE
    )


def test_valid_constructs_render_only_scoring_visible_fields():
    crm, _ = _parse("crm", _crm())
    arm, _ = _parse("arm", _arm())
    assert '"support":' not in render_memory("crm", crm)
    assert '"support":' not in render_memory("arm", arm)
    assert '"effect": "supports"' in render_memory("crm", crm)
    assert '"sufficient"' in render_memory("arm", arm)


@pytest.mark.parametrize(
    "text",
    [
        "full credit",
        "partial credit",
        "no credit",
        "pass/fail",
        "high grade",
        "maximum score",
        "automatically correct",
        "automatically incorrect",
        "deduct points",
        "2 points",
        "2 out of 3 points",
        "2/3 score",
        "score of 2",
    ],
)
def test_rejects_numeric_or_absolute_grade_policy(text: str):
    with pytest.raises(ValueError, match="grade policy"):
        _parse("crm", _crm(guidance=f"Photosynthesis should receive {text}."))


def test_allows_scientific_numbers_without_grade_context():
    items, _ = _parse(
        "crm",
        _crm(guidance="Photosynthesis can produce 2 ATP in this example."),
    )
    assert items[0]["guidance"].startswith("Photosynthesis can produce 2 ATP")


def test_rejects_crm_without_required_future_if_form():
    with pytest.raises(ValueError, match="must begin"):
        _parse("crm", _crm(condition="Light energy in photosynthesis"))


@pytest.mark.parametrize(
    "condition",
    [
        "If a future answer shows overall photosynthesis quality",
        "If a future answer addresses the photosynthesis rubric",
        "If a future answer satisfies a photosynthesis criterion",
        "If a future answer demonstrates a broad photosynthesis dimension",
    ],
)
def test_rejects_broad_dimension_crm(condition: str):
    with pytest.raises(ValueError, match="atomic local feature"):
        _parse("crm", _crm(condition=condition))


@pytest.mark.parametrize(
    "criterion",
    [
        "If a future answer mentions light energy",
        "Photosynthesis quality when a future answer mentions light energy",
        "Photosynthesis response behavior",
        "Photosynthesis student answer quality",
    ],
)
def test_rejects_arm_conditional_rule_as_cross_construct_structure(criterion: str):
    with pytest.raises(ValueError, match="must not be an IF rule"):
        _parse("arm", _arm(criterion=criterion))


def test_rejects_generic_or_content_free_arm_anchors():
    anchors = {
        "sufficient": "correct",
        "partial": "partially correct",
        "missing": "no evidence",
    }
    with pytest.raises(ValueError, match="question content"):
        _parse("arm", _arm(anchors=anchors))


def test_allows_basic_plural_form_of_question_content():
    anchors = {
        "sufficient": "All packets include light energy evidence.",
        "partial": "Some packets include light energy evidence.",
        "missing": "No packets include light energy evidence.",
    }
    items, _ = _parse("arm", _arm(anchors=anchors))
    assert items[0]["anchors"] == anchors


def test_allows_basic_verb_form_of_question_content():
    anchors = {
        "sufficient": "Packets are forwarded with light energy evidence.",
        "partial": "Packets are forwarding light energy but omit carbon dioxide.",
        "missing": "Packets are dropped without light energy evidence.",
    }
    items, _ = _parse("arm", _arm(anchors=anchors))
    assert items[0]["anchors"] == anchors


def test_arm_anchor_budget_error_reports_item_and_observed_count():
    anchors = {
        "sufficient": "Light energy makes glucose from carbon dioxide in photosynthesis and oxygen.",
        "partial": "Light energy is present but glucose and oxygen formation are unclear for photosynthesis.",
        "missing": "Light energy and carbon dioxide are absent from the photosynthesis explanation and mechanism.",
    }
    with pytest.raises(
        ValueError, match=r"ARM item 1 anchors use \d+ tokens; limit is 42"
    ):
        _parse("arm", _arm(anchors=anchors))


def test_rejects_content_free_memory_core():
    with pytest.raises(ValueError, match="question content"):
        _parse("crm", _crm(condition="If a future answer is clearly written"))


def test_schema_rejects_extra_fields_and_missing_anchor():
    with pytest.raises(Exception):
        _parse("crm", _crm(extra="forbidden"))
    value = _arm()
    del value["rubric"][0]["anchors"]["partial"]
    with pytest.raises(Exception):
        _parse("arm", value)


def test_document_memory_examples_pass_the_real_semantic_gate():
    text = (AI_ROOT / "docs" / "experiment" / "r20-prompt-design.md").read_text()
    examples = [
        json.loads(value)
        for value in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    ]
    assert len(examples) == 3
    parse_memory_update("crm", examples[1], "An unrelated answer.", QUESTION, REFERENCE)
    parse_memory_update("arm", examples[2], "An unrelated answer.", QUESTION, REFERENCE)
