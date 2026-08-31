from __future__ import annotations

from collections import Counter

import pytest

from app.experiment.r20.dataset import SAF_ARCHIVE_SHA256, build_frozen_dataset
from app.experiment.r20.runner import score_messages


@pytest.fixture(scope="module")
def frozen():
    return build_frozen_dataset("/Users/mac/Desktop/SURF/data/SAF2_0.zip")


def test_official_split_counts_roles_and_sealed_questions(frozen):
    assert frozen.manifest["archive_sha256"] == SAF_ARCHIVE_SHA256
    assert frozen.manifest["raw_counts"] == {
        "test_unseen_answers": 375,
        "test_unseen_questions": 479,
        "train": 1700,
        "validation": 427,
    }
    assert Counter(frozen.roles.values()) == {
        "formal": 8,
        "development": 2,
        "pilot_run": 2,
        "excluded": 14,
        "sealed_unseen_question": 5,
    }
    assert frozen.manifest["mapped_counts"] == {
        "ambiguous_hf_split": 44,
        "test_unseen_answers": 375,
        "test_unseen_questions": 479,
        "train": 1665,
        "validation": 418,
    }
    assert frozen.manifest["clean_counts"] == {
        "test_unseen_answers": 349,
        "test_unseen_questions": 479,
        "train": 1619,
        "validation": 406,
    }
    assert set(q for q, role in frozen.roles.items() if role == "formal") == {
        "1.6",
        "2.4",
        "5.11",
        "6.3",
        "4.3",
        "4.1_LM_v1.0",
        "6.3_IPP",
        "8.1_MM",
    }
    assert set(q for q, role in frozen.roles.items() if role == "pilot_run") == {
        "5.7",
        "4.13",
    }
    for question, paths in frozen.trajectories.items():
        expected = 60 if frozen.roles[question] == "formal" else 20
        assert all(len(ids) == len(set(ids)) == expected for ids in paths.values())
        assert len(frozen.test_endpoints[question]) == (15 if expected == 60 else 5)
        assert len(frozen.probes[question]) == 5


def test_trajectories_are_deterministic_and_do_not_use_validation(frozen):
    again = build_frozen_dataset("/Users/mac/Desktop/SURF/data/SAF2_0.zip")
    assert frozen.manifest["manifest_sha256"] == again.manifest["manifest_sha256"]
    by_id = {row.answer_id: row for row in frozen.records}
    for question, paths in frozen.trajectories.items():
        assert set(paths) == {"1", "2", "3"}
        expected = 60 if frozen.roles[question] == "formal" else 20
        first = set(paths["1"])
        for ids in paths.values():
            assert len(ids) == len(set(ids)) == expected
            assert set(ids) == first
            assert all(
                by_id[value].source_split in {"train", "validation"} for value in ids
            )
        assert set(frozen.probes[question]).issubset(frozen.test_endpoints[question])


def test_no_clean_train_test_question_group_leakage(frozen):
    clean = [row for row in frozen.records if row.excluded_reason is None]
    train = {
        (row.question_id, row.group_id)
        for row in clean
        if row.source_split in {"train", "validation"}
    }
    test = {
        (row.question_id, row.group_id)
        for row in clean
        if row.source_split == "test_unseen_answers"
    }
    assert train.isdisjoint(test)


def test_scoring_payload_is_an_explicit_safe_allowlist(frozen):
    question = next(q for q, role in frozen.roles.items() if role == "formal")
    row = next(
        row
        for row in frozen.records
        if row.answer_id in frozen.test_endpoints[question]
    )
    payload = score_messages(
        "score",
        row.question_text,
        row.reference_answer,
        row.student_answer,
        frozen.max_scores[question],
        "[]",
    )[1]["content"]
    assert row.teacher_feedback not in payload
    assert '"group_id"' not in payload
    assert "teacher_score" not in payload
