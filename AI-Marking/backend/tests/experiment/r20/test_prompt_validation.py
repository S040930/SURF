from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.dataset import build_frozen_dataset
from app.experiment.r20.prompt_validation import (
    EXPECTED_VALIDATION_CALLS,
    process_validation_call,
    validation_specs,
)
from app.experiment.r20.protocol import (
    ARMUpdateOutput,
    CRMUpdateOutput,
    PromptTemplates,
    ScoreOutput,
)
from app.models.r20 import R20ModelConfig, R20PromptValidationSuite, R20PromptVersion

WORKSPACE = Path(__file__).resolve().parents[5]


def _dataset():
    return build_frozen_dataset(
        WORKSPACE / "data" / "SAF2_0.zip",
        WORKSPACE / "data" / "saf_hf_split_map.csv",
    )


def test_validation_suite_has_fixed_48_call_order():
    dataset = _dataset()
    specs = validation_specs(dataset, ["model-b", "model-a"])
    assert len(specs) == EXPECTED_VALIDATION_CALLS == 48

    questions = sorted(
        question for question, role in dataset.roles.items() if role == "development"
    )
    for model in ("model-a", "model-b"):
        for question in questions:
            block = [
                spec
                for spec in specs
                if spec.model_config_id == model and spec.question_id == question
            ]
            assert [(spec.condition, spec.scenario) for spec in block[:6]] == [
                ("crm", "train_high"),
                ("crm", "train_low"),
                ("crm", "train_middle"),
                ("arm", "train_high"),
                ("arm", "train_low"),
                ("arm", "train_middle"),
            ]
            assert [(spec.condition, spec.scenario) for spec in block[6:]] == [
                ("nm", "validation_low"),
                ("nm", "validation_high"),
                ("crm", "validation_low"),
                ("crm", "validation_high"),
                ("arm", "validation_low"),
                ("arm", "validation_high"),
            ]


def test_validation_selection_is_deterministic_and_uses_distinct_strata():
    dataset = _dataset()
    first = validation_specs(dataset, ["model-b", "model-a"])
    second = validation_specs(dataset, ["model-a", "model-b"])
    assert first == second
    for model in ("model-a", "model-b"):
        for question in sorted(
            question
            for question, role in dataset.roles.items()
            if role == "development"
        ):
            selected = [
                spec.answer_id
                for spec in first
                if spec.model_config_id == model
                and spec.question_id == question
                and spec.condition == "crm"
                and spec.template_kind == "crm_update"
            ]
            assert len(selected) == 3
            assert len(set(selected)) == 3


def test_scripted_suite_persists_all_48_calls_and_respects_boundaries(
    db_session, monkeypatch
):
    prompt = R20PromptVersion(
        id=str(uuid4()),
        protocol_id=PROTOCOL_ID,
        name="scripted",
        templates_json=PromptTemplates(
            scoring="score", crm_update="crm", arm_update="arm"
        ).model_dump(mode="json"),
        templates_sha256="p" * 64,
        status="draft",
    )
    models = []
    for index in range(2):
        model = R20ModelConfig(
            id=str(uuid4()),
            name=f"model-{index}",
            config_json={
                "provider": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "requested_model": f"model-{index}",
                "expected_returned_model": f"model-{index}",
                "api_key_env": "",
                "temperature": 0,
                "timeout_seconds": 60,
                "input_cost_fen_per_million": 0,
                "output_cost_fen_per_million": 0,
            },
            config_sha256=str(index) * 64,
            status="frozen",
        )
        models.append(model)
        db_session.add(model)
    suite = R20PromptValidationSuite(
        id=str(uuid4()),
        protocol_id=PROTOCOL_ID,
        prompt_version_id=prompt.id,
        model_config_ids_json=[model.id for model in models],
        status="running",
        expected_calls=48,
        completed_calls=0,
    )
    db_session.add_all([prompt, suite])
    db_session.commit()
    calls = []

    async def scripted_complete(request):
        payload = json.loads(request.messages[1]["content"])
        calls.append((request.output_schema, payload))
        if request.output_schema is ScoreOutput:
            assert "teacher_score" not in payload
            assert "teacher_feedback" not in payload
            assert '"support":' not in payload["memory"]
            parsed = ScoreOutput(score=0, feedback="Observable evidence summarized.")
        else:
            reference_words = re.findall(r"[A-Za-z]{4,}", payload["reference_answer"])
            term = max(reference_words, key=len)
            if request.output_schema is CRMUpdateOutput:
                parsed = CRMUpdateOutput.model_validate(
                    {
                        "rules": [
                            {
                                "condition": f"If a future answer addresses {term}",
                                "effect": "supports",
                                "importance": "major",
                                "guidance": f"{term} is relevant to the answer.",
                                "support": "Teacher evidence supports this qualitative pattern.",
                            }
                        ]
                    }
                )
            else:
                parsed = ARMUpdateOutput.model_validate(
                    {
                        "rubric": [
                            {
                                "criterion": f"Explanation of {term}",
                                "importance": "major",
                                "anchors": {
                                    "sufficient": f"{term} is explained.",
                                    "partial": f"{term} is mentioned.",
                                    "missing": f"{term} is absent.",
                                },
                                "support": "Teacher evidence supports this qualitative dimension.",
                            }
                        ]
                    }
                )
        if request.result_validator is not None:
            request.result_validator(parsed)
        return SimpleNamespace(parsed=parsed)

    monkeypatch.setattr(
        "app.experiment.r20.prompt_validation.complete_request", scripted_complete
    )
    for _ in range(EXPECTED_VALIDATION_CALLS):
        process_validation_call(db_session, suite)
    db_session.refresh(suite)
    assert suite.status == "passed"
    assert suite.completed_calls == EXPECTED_VALIDATION_CALLS
    assert len(calls) == EXPECTED_VALIDATION_CALLS

    updates = [payload for schema, payload in calls if schema is not ScoreOutput]
    for offset in range(0, len(updates), 3):
        assert [
            len(payload["existing_memory"]) for payload in updates[offset : offset + 3]
        ] == [0, 1, 1]
