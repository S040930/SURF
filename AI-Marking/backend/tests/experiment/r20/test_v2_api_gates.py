from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.r20_platform import (
    create_prompt_validation_suite,
    create_prompt_version,
    delete_prompt_version,
    freeze_prompt_version,
)
from app.experiment.r20 import LEGACY_PROTOCOL_ID, PROTOCOL_ID
from app.experiment.r20.prompts import NORMATIVE_TEMPLATES_SHA256
from app.models.r20 import (
    R20ModelConfig,
    R20Project,
    R20PromptTrial,
    R20PromptValidationSuite,
    R20PromptVersion,
)
from app.schemas.r20 import R20PromptValidationSuiteIn, R20PromptVersionIn

AI_ROOT = Path(__file__).resolve().parents[4]


def _normative_payload(name="v2 normative") -> R20PromptVersionIn:
    text = (AI_ROOT / "docs" / "experiment" / "r20-prompt-design.md").read_text()
    scoring, crm, arm = re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    return R20PromptVersionIn(
        name=name, scoring=scoring, crm_update=crm, arm_update=arm
    )


def _model(db, name: str) -> R20ModelConfig:
    row = R20ModelConfig(
        id=str(uuid4()),
        name=name,
        config_json={
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "requested_model": name,
            "expected_returned_model": name,
            "api_key_env": "",
            "temperature": 0,
            "timeout_seconds": 60,
            "input_cost_fen_per_million": 0,
            "output_cost_fen_per_million": 0,
        },
        config_sha256=name.ljust(64, "0")[:64],
        status="frozen",
    )
    db.add(row)
    return row


def test_candidate_normalizes_clipboard_newlines_then_matches_normative(db_session):
    payload = _normative_payload()
    created = create_prompt_version(payload, db_session)
    assert created.protocol_id == PROTOCOL_ID
    assert created.templates_sha256 == NORMATIVE_TEMPLATES_SHA256

    copied = payload.model_copy(
        update={
            "scoring": "\n" + payload.scoring.replace("\n", "\r\n") + "\r\n",
            "crm_update": payload.crm_update + "\n",
        }
    )
    copied_row = create_prompt_version(copied, db_session)
    assert copied_row.templates_sha256 == NORMATIVE_TEMPLATES_SHA256
    assert copied_row.templates_json["scoring"] == payload.scoring

    changed = payload.model_copy(update={"scoring": payload.scoring + " "})
    with pytest.raises(HTTPException) as caught:
        create_prompt_version(changed, db_session)
    assert caught.value.status_code == 422
    assert "共同评分提示词" in caught.value.detail
    assert "CRM 更新提示词" not in caught.value.detail


def test_suite_requires_two_frozen_models_and_is_persisted(db_session):
    prompt = create_prompt_version(_normative_payload(), db_session)
    first = _model(db_session, "model-a")
    second = _model(db_session, "model-b")
    db_session.commit()
    suite = create_prompt_validation_suite(
        prompt.id,
        R20PromptValidationSuiteIn(model_config_ids=[second.id, first.id]),
        db_session,
    )
    assert suite.status == "queued"
    assert suite.expected_calls == 48
    assert suite.model_config_ids_json == sorted([first.id, second.id])

    with pytest.raises(HTTPException) as caught:
        create_prompt_validation_suite(
            prompt.id,
            R20PromptValidationSuiteIn(model_config_ids=[first.id, second.id]),
            db_session,
        )
    assert caught.value.status_code == 409


def test_v1_prompt_cannot_satisfy_v2_freeze_gate(db_session):
    prompt = R20PromptVersion(
        id=str(uuid4()),
        protocol_id=LEGACY_PROTOCOL_ID,
        name="historical",
        templates_json={"scoring": "s", "crm_update": "c", "arm_update": "a"},
        templates_sha256="x" * 64,
        status="draft",
    )
    db_session.add(prompt)
    db_session.commit()
    with pytest.raises(HTTPException, match="v1"):
        freeze_prompt_version(prompt.id, db_session)


def test_freeze_does_not_require_validation_suite(db_session):
    prompt = create_prompt_version(_normative_payload(), db_session)
    frozen = freeze_prompt_version(prompt.id, db_session)
    assert frozen.status == "frozen"
    assert frozen.validated_model_config_ids_json is None


def test_delete_prompt_version_removes_trials_and_suites(db_session):
    prompt = create_prompt_version(_normative_payload(), db_session)
    first = _model(db_session, "model-a")
    second = _model(db_session, "model-b")
    suite = R20PromptValidationSuite(
        id=str(uuid4()),
        protocol_id=PROTOCOL_ID,
        prompt_version_id=prompt.id,
        model_config_ids_json=sorted([first.id, second.id]),
        status="failed",
        expected_calls=48,
        completed_calls=10,
    )
    db_session.add(suite)
    db_session.add(
        R20PromptTrial(
            id=str(uuid4()),
            suite_id=suite.id,
            prompt_version_id=prompt.id,
            model_config_id=first.id,
            template_kind="scoring",
            question_id="q",
            answer_id="a",
            input_json={},
            valid=False,
        )
    )
    db_session.commit()
    delete_prompt_version(prompt.id, db_session)
    assert db_session.get(R20PromptVersion, prompt.id) is None
    assert db_session.get(R20PromptValidationSuite, suite.id) is None
    assert (
        db_session.scalar(
            select(R20PromptTrial).where(R20PromptTrial.prompt_version_id == prompt.id)
        )
        is None
    )


def test_delete_prompt_version_allows_frozen_unreferenced(db_session):
    prompt = create_prompt_version(_normative_payload(), db_session)
    first = _model(db_session, "model-a")
    second = _model(db_session, "model-b")
    db_session.add(
        R20PromptValidationSuite(
            id=str(uuid4()),
            protocol_id=PROTOCOL_ID,
            prompt_version_id=prompt.id,
            model_config_ids_json=sorted([first.id, second.id]),
            status="passed",
            expected_calls=48,
            completed_calls=48,
        )
    )
    db_session.commit()
    freeze_prompt_version(prompt.id, db_session)
    delete_prompt_version(prompt.id, db_session)
    assert db_session.get(R20PromptVersion, prompt.id) is None


def test_delete_prompt_version_guards(db_session):
    with pytest.raises(HTTPException) as caught:
        delete_prompt_version(str(uuid4()), db_session)
    assert caught.value.status_code == 404

    prompt = create_prompt_version(_normative_payload(), db_session)
    db_session.add(
        R20PromptValidationSuite(
            id=str(uuid4()),
            protocol_id=PROTOCOL_ID,
            prompt_version_id=prompt.id,
            model_config_ids_json=["a", "b"],
            status="running",
            expected_calls=48,
            completed_calls=10,
        )
    )
    db_session.commit()
    with pytest.raises(HTTPException, match="验证任务在运行"):
        delete_prompt_version(prompt.id, db_session)
    assert db_session.get(R20PromptVersion, prompt.id) is not None

    for suite in db_session.scalars(
        select(R20PromptValidationSuite).where(
            R20PromptValidationSuite.prompt_version_id == prompt.id
        )
    ):
        db_session.delete(suite)
    db_session.commit()
    frozen = create_prompt_version(_normative_payload("referenced"), db_session)
    db_session.add(
        R20Project(
            id=str(uuid4()),
            name="referencing project",
            kind="pilot_run",
            model_config_ids_json=["a", "b"],
            model_configs_json=[],
            model_configs_sha256="m" * 64,
            prompt_version_id=frozen.id,
            prompt_version_name=frozen.name,
            prompt_templates_json=frozen.templates_json,
            prompt_version_sha256=frozen.templates_sha256,
            manifest_json={"expected_calls": 48},
            manifest_sha256="x" * 64,
            data_sha256="d" * 64,
            analysis_code_sha256="a" * 64,
        )
    )
    db_session.commit()
    with pytest.raises(HTTPException, match="已被项目引用"):
        delete_prompt_version(frozen.id, db_session)
    assert db_session.get(R20PromptVersion, frozen.id) is not None
