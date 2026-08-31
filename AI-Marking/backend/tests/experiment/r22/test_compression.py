from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.experiment.r20.tokenization import token_count
from app.experiment.r22.compression import (
    CompressionValidationError,
    compress_candidate,
    inspect_candidate,
)
from app.experiment.r22.protocol import MAX_FEEDBACK_TOKENS
from app.experiment.r22.recovery import TerminalRecoveryError, recover_candidate


def _tokens(count: int, word: str = "x") -> str:
    value = " ".join(word for _ in range(count))
    if word == "x":
        assert token_count(value) == count
    return value


class FakeRunner:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def run(self, *, messages, schema, runtime):
        self.calls += 1
        return SimpleNamespace(value=self.value, latency_ms=7)


def test_score_overflow_is_captured_by_transport_and_classified_for_compression():
    candidate = {"score": 2.0, "feedback": _tokens(MAX_FEEDBACK_TOKENS + 1)}
    check = inspect_candidate("test_score", None, candidate)
    assert check.needs_compression
    assert check.violations[0].field == "feedback"


def test_score_compression_preserves_numeric_score_and_runs_once():
    candidate = {"score": 2.0, "feedback": _tokens(MAX_FEEDBACK_TOKENS + 1)}
    compressed = {"score": 2.0, "feedback": "short feedback"}
    runner = FakeRunner(compressed)
    result = compress_candidate(
        runner,
        kind="test_score",
        condition=None,
        value=candidate,
        runtime={"model": "frozen", "reasoning_effort": "high"},
    )
    assert result.compressed == compressed
    assert result.original["score"] == result.compressed["score"]
    assert runner.calls == 1


def test_crm_overflow_allows_only_text_rewrite():
    candidate = {
        "rules": [
            {
                "condition": "If a future answer explains photosynthesis",
                "effect": "supports",
                "importance": "major",
                "guidance": _tokens(73),
                "support": "teacher evidence",
            }
        ]
    }
    check = inspect_candidate(
        "memory_update",
        "crm",
        candidate,
        answer="unrelated answer",
        question="Explain photosynthesis",
        reference_answer="Photosynthesis uses light energy",
    )
    assert check.needs_compression
    assert any(item.field.endswith("effect+guidance") for item in check.violations)
    runner = FakeRunner(
        {
            "rules": [
                {
                    "condition": candidate["rules"][0]["condition"],
                    "effect": "supports",
                    "importance": "major",
                    "guidance": "photosynthesis uses light",
                    "support": "teacher evidence",
                }
            ]
        }
    )
    result = compress_candidate(
        runner,
        kind="memory_update",
        condition="crm",
        value=candidate,
        runtime={},
        answer="unrelated answer",
        question="Explain photosynthesis",
        reference_answer="Photosynthesis uses light energy",
    )
    assert result.compressed["rules"][0]["effect"] == "supports"
    assert result.compressed["rules"][0]["importance"] == "major"


def test_arm_anchor_overflow_is_recovered_without_changing_item_shape():
    candidate = {
        "rubric": [
            {
                "criterion": "photosynthesis mechanism",
                "importance": "major",
                "anchors": {
                    "sufficient": _tokens(25, "photosynthesis"),
                    "partial": _tokens(25, "photosynthesis"),
                    "missing": _tokens(25, "photosynthesis"),
                },
                "support": "teacher evidence",
            }
        ]
    }
    check = inspect_candidate(
        "memory_update",
        "arm",
        candidate,
        answer="unrelated answer",
        question="Explain photosynthesis",
        reference_answer="Photosynthesis uses light energy",
    )
    assert check.needs_compression
    compressed = {
        "rubric": [
            {
                "criterion": "photosynthesis mechanism",
                "importance": "major",
                "anchors": {
                    "sufficient": "photosynthesis is explained",
                    "partial": "photosynthesis is incomplete",
                    "missing": "photosynthesis is absent",
                },
                "support": "teacher evidence",
            }
        ]
    }
    result = compress_candidate(
        FakeRunner(compressed),
        kind="memory_update",
        condition="arm",
        value=candidate,
        runtime={},
        answer="unrelated answer",
        question="Explain photosynthesis",
        reference_answer="Photosynthesis uses light energy",
    )
    assert len(result.compressed["rubric"]) == 1
    assert list(result.compressed["rubric"][0]["anchors"]) == ["sufficient", "partial", "missing"]


def test_protected_field_change_is_rejected():
    candidate = {"score": 1.0, "feedback": _tokens(MAX_FEEDBACK_TOKENS + 1)}
    with pytest.raises(CompressionValidationError, match="protected"):
        compress_candidate(
            FakeRunner({"score": 2.0, "feedback": "short"}),
            kind="test_score",
            condition=None,
            value=candidate,
            runtime={},
        )


def test_non_token_error_is_terminal_and_does_not_call_compressor():
    runner = FakeRunner({"score": 1.0, "feedback": "never used"})
    with pytest.raises(TerminalRecoveryError):
        recover_candidate(
            runner,
            kind="test_score",
            condition=None,
            primary_value={"score": 9.0, "feedback": "invalid score"},
            runtime={},
        )
    assert runner.calls == 0


def test_recovery_invokes_at_most_one_compression_call():
    candidate = {"score": 1.0, "feedback": _tokens(MAX_FEEDBACK_TOKENS + 1)}
    runner = FakeRunner({"score": 1.0, "feedback": "short"})
    outcome = recover_candidate(
        runner,
        kind="test_score",
        condition=None,
        primary_value=candidate,
        runtime={},
    )
    assert outcome.compressed
    assert runner.calls == 1
