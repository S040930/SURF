import random

import pytest

from app.core.config import settings
from app.experiment.memory_study.dataset import build_dataset
from app.experiment.memory_study.protocol import (
    PROTOCOL_ID,
    V3_R2_FORMAL_QUESTIONS,
    V3_R2_PROTOCOL_ID,
)


@pytest.fixture(scope="module")
def formal():
    return build_dataset(
        settings.MEMORY_STUDY_ARCHIVE_PATH,
        kind="formal",
        protocol_id=PROTOCOL_ID,
    )


@pytest.fixture(scope="module")
def v3_r2_formal():
    return build_dataset(
        settings.MEMORY_STUDY_ARCHIVE_PATH,
        kind="formal",
        protocol_id=V3_R2_PROTOCOL_ID,
    )


def test_new_seed_selects_fixed_mutually_exclusive_formal_sample(formal):
    assert len(formal.selected_questions) == 6
    assert len(formal.records) == 6 * (40 + 10)
    assert set(formal.orders) == set(formal.selected_questions)
    for question in formal.selected_questions:
        assert len(formal.orders[question]["training"]) == 40
        assert len(formal.orders[question]["test"]) == 10
        assert all(
            len(formal.orders[question][f"order_{index}"]) == 40 for index in (1, 2, 3)
        )
        assert set(formal.orders[question]["training"]).isdisjoint(
            formal.orders[question]["test"]
        )


def test_sampling_is_reproducible_and_uses_training_for_max_score(formal):
    again = build_dataset(
        settings.MEMORY_STUDY_ARCHIVE_PATH,
        kind="formal",
        protocol_id=PROTOCOL_ID,
    )
    assert formal.manifest["manifest_sha256"] == again.manifest["manifest_sha256"]
    assert formal.manifest["roles"] == again.manifest["roles"]
    assert formal.max_scores == again.max_scores
    for row in formal.records:
        assert row.verification_feedback
        assert row.selection_kind in {"training", "test"}
        if row.selection_kind == "test":
            assert row.source_split == "unseen_answers"


def test_score_ceilings_are_frozen_question_grid_ceiling(formal):
    assert set(formal.score_ceilings) == set(formal.selected_questions)
    assert set(formal.score_floors) == set(formal.selected_questions)
    assert "score_ceilings" in formal.manifest
    assert "score_floors" in formal.manifest
    assert "score_range_rule" in formal.manifest
    observed_by_question: dict[str, tuple[float, float]] = {}
    for row in formal.records:
        low, high = observed_by_question.get(row.question_id, (float("inf"), float("-inf")))
        observed_by_question[row.question_id] = (
            min(low, row.teacher_score),
            max(high, row.teacher_score),
        )
    for question in formal.selected_questions:
        observed_min, observed_max = observed_by_question[question]
        # The registered grid spans at least everything observed for the
        # question (selected records come from the same archive pool).
        assert formal.score_floors[question] <= observed_min
        assert formal.score_ceilings[question] >= observed_max
        # The registered ceiling covers the training-observed max.
        assert formal.score_ceilings[question] >= formal.max_scores[question]
        assert formal.score_floors[question] >= 0.0
        assert formal.score_floors[question] <= formal.score_ceilings[question]
        # Manifest parity.
        assert (
            formal.manifest["score_ceilings"][question]
            == formal.score_ceilings[question]
        )
        assert (
            formal.manifest["score_floors"][question]
            == formal.score_floors[question]
        )


def test_v3_r2_uses_the_frozen_six_question_set(v3_r2_formal):
    assert set(v3_r2_formal.selected_questions) == set(V3_R2_FORMAL_QUESTIONS)
    assert len(v3_r2_formal.records) == 6 * (40 + 10)
    assert "4.2_LM_v1.0" not in v3_r2_formal.selected_questions
    assert set(v3_r2_formal.roles) >= set(V3_R2_FORMAL_QUESTIONS)
    assert set(v3_r2_formal.manifest["question_hashes"]) == set(
        V3_R2_FORMAL_QUESTIONS
    )
    assert v3_r2_formal.manifest["protocol_fingerprint"]
    for question in V3_R2_FORMAL_QUESTIONS:
        assert len(v3_r2_formal.orders[question]["training"]) == 40
        assert len(v3_r2_formal.orders[question]["test"]) == 10
        assert set(v3_r2_formal.orders[question]["training"]).isdisjoint(
            v3_r2_formal.orders[question]["test"]
        )
        assert set(v3_r2_formal.orders[question]) == {
            "training",
            "test",
            "original",
            "shuffled",
        }
        records = v3_r2_formal.records_for(question, "training")
        expected_original = [
            row.answer_id
            for row in sorted(
                records,
                key=lambda row: (row.source_path, row.source_position, row.answer_id),
            )
        ]
        assert v3_r2_formal.orders[question]["original"] == expected_original
        expected_shuffled = expected_original[:]
        random.Random(42).shuffle(expected_shuffled)
        assert v3_r2_formal.orders[question]["shuffled"] == expected_shuffled
        assert v3_r2_formal.manifest["order_variants"] == [
            "original",
            "shuffled",
        ]
        assert v3_r2_formal.manifest["order_shuffle_seed"] == 42
