from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.r20_platform import (
    create_project,
    freeze_project,
    freeze_prompt_version,
)
from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.prompts import templates_hash
from app.experiment.r20.protocol import PromptTemplates
from app.models.r20 import (
    R20ModelConfig,
    R20PromptVersion,
)
from app.schemas.r20 import R20ProjectCreate

FIXTURE_TEMPLATES = PromptTemplates(
    scoring="Grade using the question and reference answer only.",
    crm_update="Return a compact replacement rule snapshot.",
    arm_update="Return a compact replacement rubric snapshot.",
)


def _model(db, requested: str):
    row = R20ModelConfig(
        id=str(uuid4()),
        name=requested,
        config_json={
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "requested_model": requested,
            "expected_returned_model": requested,
            "api_key_env": "",
            "temperature": 0,
            "timeout_seconds": 60,
            "input_cost_fen_per_million": 0,
            "output_cost_fen_per_million": 0,
        },
        config_sha256=requested.ljust(64, "0")[:64],
        status="draft",
    )
    db.add(row)
    return row


def test_pilot_and_formal_can_be_created_without_binding(db_session):
    model_a = _model(db_session, "provider-a-model")
    model_b = _model(db_session, "provider-b-model")
    prompt = R20PromptVersion(
        id=str(uuid4()),
        name="r20",
        templates_json=FIXTURE_TEMPLATES.model_dump(mode="json"),
        templates_sha256=templates_hash(FIXTURE_TEMPLATES),
        status="draft",
    )
    model_a.status = "frozen"
    model_b.status = "frozen"
    prompt.status = "frozen"
    prompt.validated_model_config_ids_json = sorted([model_a.id, model_b.id])
    db_session.add(prompt)
    db_session.commit()
    created = create_project(
        R20ProjectCreate(
            name="pilot",
            kind="pilot_run",
            model_config_ids=[model_a.id, model_b.id],
            prompt_version_id=prompt.id,
        ),
        db_session,
    )
    assert created["kind"] == "pilot_run"
    assert created["model_config_ids_json"] == [model_a.id, model_b.id]
    formal = create_project(
        R20ProjectCreate(
            name="formal",
            kind="formal",
            model_config_ids=[model_a.id, model_b.id],
            prompt_version_id=prompt.id,
        ),
        db_session,
    )
    assert formal["kind"] == "formal"
    assert formal["pilot_project_id"] is None


def test_formal_signature_is_order_independent_and_single_use(db_session):
    from app.models.r20 import R20Project

    deepseek = _model(db_session, "deepseek-v4-flash-ga-260731")
    doubao = _model(db_session, "doubao-seed-2.0-lite")
    deepseek.status = doubao.status = "frozen"
    prompt = R20PromptVersion(
        id=str(uuid4()),
        name="formal prompt",
        templates_json=FIXTURE_TEMPLATES.model_dump(mode="json"),
        templates_sha256=templates_hash(FIXTURE_TEMPLATES),
        status="frozen",
        validated_model_config_ids_json=sorted([deepseek.id, doubao.id]),
    )
    db_session.add(prompt)
    db_session.commit()
    pilot_created = create_project(
        R20ProjectCreate(
            name="completed pilot",
            kind="pilot_run",
            model_config_ids=[deepseek.id, doubao.id],
            prompt_version_id=prompt.id,
        ),
        db_session,
    )
    pilot = db_session.get(R20Project, pilot_created["id"])
    pilot.status = "completed"
    db_session.commit()
    first = create_project(
        R20ProjectCreate(
            name="only formal",
            kind="formal",
            model_config_ids=[deepseek.id, doubao.id],
            prompt_version_id=prompt.id,
            pilot_project_id=pilot.id,
        ),
        db_session,
    )
    assert first["kind"] == "formal"
    with pytest.raises(HTTPException) as caught:
        create_project(
            R20ProjectCreate(
                name="duplicate formal",
                kind="formal",
                model_config_ids=[doubao.id, deepseek.id],
                prompt_version_id=prompt.id,
                pilot_project_id=pilot.id,
            ),
            db_session,
        )
    assert caught.value.status_code == 409


def test_freeze_materializes_dynamic_grid_without_validation_or_unseen_questions(
    db_session, monkeypatch
):
    deepseek = _model(db_session, "deepseek-v4-flash-ga-260731")
    doubao = _model(db_session, "doubao-seed-2.0-lite")
    prompt = R20PromptVersion(
        id=str(uuid4()),
        name="r20 freeze",
        templates_json=FIXTURE_TEMPLATES.model_dump(mode="json"),
        templates_sha256=templates_hash(FIXTURE_TEMPLATES),
        status="draft",
    )
    deepseek.status = "frozen"
    doubao.status = "frozen"
    prompt.status = "frozen"
    prompt.validated_model_config_ids_json = sorted([deepseek.id, doubao.id])
    db_session.add(prompt)
    db_session.commit()
    created = create_project(
        R20ProjectCreate(
            name="freeze pilot",
            kind="pilot_run",
            model_config_ids=[deepseek.id, doubao.id],
            prompt_version_id=prompt.id,
        ),
        db_session,
    )
    monkeypatch.setattr(
        "app.api.r20_platform.settings.R20_SAF_ARCHIVE_PATH",
        "/Users/mac/Desktop/SURF/data/SAF2_0.zip",
    )
    monkeypatch.setattr(
        "app.api.r20_platform.settings.R20_SAF_SPLIT_MAP_PATH",
        "/Users/mac/Desktop/SURF/data/saf_hf_split_map.csv",
    )
    frozen = freeze_project(created["id"], db_session)
    assert frozen["status"] == "frozen"
    assert frozen["progress"]["total"] == frozen["manifest_json"]["expected_calls"]
    from sqlalchemy import select

    from app.models.r20 import R20Record, R20Stream

    records = list(db_session.scalars(select(R20Record)))
    streams = list(db_session.scalars(select(R20Stream)))
    assert all(
        row.source_split in {"train", "validation", "test_unseen_answers"}
        for row in records
    )
    assert len(streams) == 2 * 2 * 3 * 3
    assert frozen["manifest_json"]["schedule"] == {
        "memory_count": 20,
        "checkpoints": [10, 20],
        "probe_checkpoints": [10],
        "test_count": 5,
        "final_history": 20,
    }
    assert sum(row.usage == "memory" for row in records) == 2 * 20 * 3
    assert sum(row.usage == "test" for row in records) == 2 * 5
    selected = frozen["manifest_json"]["selected_questions"]
    orders = [
        [
            (row.model_id, row.trajectory, row.condition)
            for row in sorted(
                (stream for stream in streams if stream.question_id == question),
                key=lambda row: row.order_rank,
            )
        ]
        for question in selected
    ]
    assert orders[1] == orders[0][1:] + orders[0][:1]


async def test_running_project_calls_are_blinded(client, db_session):
    from app.models.r20 import R20Project

    project = R20Project(
        id=str(uuid4()),
        name="blind",
        kind="pilot_run",
        status="running",
        model_config_ids_json=["a", "b"],
        model_configs_json=[],
        model_configs_sha256="m" * 64,
        prompt_version_id="p",
        prompt_version_name="p",
        prompt_templates_json={},
        prompt_version_sha256="p" * 64,
        manifest_json={"secret": "hidden"},
        manifest_sha256="x" * 64,
        data_sha256="d" * 64,
        analysis_code_sha256="a" * 64,
    )
    db_session.add(project)
    db_session.commit()
    response = await client.get(f"/api/r20/projects/{project.id}/calls")
    assert response.status_code == 403
    detail = (await client.get(f"/api/r20/projects/{project.id}")).json()
    assert detail["manifest_json"] == {
        "protocol": PROTOCOL_ID,
        "blinded": True,
    }


async def test_r20_config_freeze(client):
    payload = {
        "name": "DeepSeek",
        "config_json": {
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "requested_model": "deepseek-v4-flash-ga-260731",
            "expected_returned_model": "deepseek-v4-flash-ga-260731",
            "api_key_env": "",
            "temperature": 0,
            "timeout_seconds": 60,
            "input_cost_fen_per_million": 0,
            "output_cost_fen_per_million": 0,
        },
    }
    created = await client.post("/api/r20/model-configs", json=payload)
    assert created.status_code == 405


async def test_r20_frozen_config_delete_discards_trials(client, db_session):
    payload = {
        "name": "Broken",
        "config_json": {
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "requested_model": "broken-model",
            "expected_returned_model": "broken-model",
            "api_key_env": "",
            "temperature": 0,
            "timeout_seconds": 60,
            "input_cost_fen_per_million": 0,
            "output_cost_fen_per_million": 0,
        },
    }
    created = await client.post("/api/r20/model-configs", json=payload)
    assert created.status_code == 405


def test_prompt_freeze_does_not_require_validation_suite(db_session):
    first = _model(db_session, "deepseek-v4-flash-ga-260731")
    second = _model(db_session, "doubao-seed-2.0-lite")
    first.status = second.status = "frozen"
    prompt = R20PromptVersion(
        id=str(uuid4()),
        name="trial gated prompt",
        templates_json=FIXTURE_TEMPLATES.model_dump(mode="json"),
        templates_sha256=templates_hash(FIXTURE_TEMPLATES),
        status="draft",
    )
    db_session.add(prompt)
    db_session.commit()
    frozen = freeze_prompt_version(prompt.id, db_session)
    assert frozen.status == "frozen"
    assert frozen.validated_model_config_ids_json is None
