"""Runner config / site config / delete-project / worker override tests."""

import pytest

from app.experiment.memory_study.worker import _runtime_for_model
from app.models.memory_study import MSRunnerConfig, MSSiteConfig, MSStudy
from app.services.memory_study import MemoryStudyService

RUNNER_PAYLOAD = {
    "model": "gpt-6-luna",
    "reasoning_effort": "high",
    "speed_mode": "fast",
    "timeout_seconds": 300,
}


def _study(db, study_id: str = "study-1", status: str = "paused") -> MSStudy:
    study = MSStudy(
        id=study_id,
        protocol_id="saf-memory-framework-v1",
        name="test",
        kind="development",
        status=status,
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={},
        expected_json={},
        progress_json={},
        integrity_status="pending",
        integrity_json={},
        results_embargoed=True,
    )
    db.add(study)
    db.commit()
    return study


# ------------------------------------------------------------------- runners


def test_list_runner_configs_returns_luna_speed_modes_without_rows(db_session):
    rows = MemoryStudyService(db_session).list_runner_configs()
    assert len(rows) == 2
    assert [row["model"] for row in rows] == [
        "gpt-6-luna",
        "gpt-6-luna",
    ]
    assert [row["speed_mode"] for row in rows] == [
        "standard",
        "fast",
    ]
    assert all(row["reasoning_effort"] == "medium" for row in rows)
    assert all(row["timeout_seconds"] == 120 for row in rows)


async def test_runner_config_crud_and_validation(client):
    # create duplicate conflict
    created = await client.post("/api/memory-study/runners", json=RUNNER_PAYLOAD)
    assert created.status_code == 201
    assert created.json()["speed_mode"] == "fast"

    duplicate = await client.post("/api/memory-study/runners", json=RUNNER_PAYLOAD)
    assert duplicate.status_code == 409

    # the same model can carry both standard and fast rows
    same_model_standard = {
        "model": "gpt-6-luna",
        "reasoning_effort": "medium",
        "speed_mode": "standard",
        "timeout_seconds": 120,
    }
    assert (
        await client.post("/api/memory-study/runners", json=same_model_standard)
    ).status_code == 201

    # model outside frozen slots
    bad_model = {
        "model": "gpt-4o",
        "reasoning_effort": "medium",
        "speed_mode": "standard",
        "timeout_seconds": 120,
    }
    assert (await client.post("/api/memory-study/runners", json=bad_model)).status_code == 422

    # speed_mode not one of standard/fast
    bad_speed = {**RUNNER_PAYLOAD, "model": "gpt-6-luna", "speed_mode": "turbo"}
    assert (await client.post("/api/memory-study/runners", json=bad_speed)).status_code == 422

    # update
    updated = await client.put(
        "/api/memory-study/runners/gpt-6-luna/fast",
        json={
            "reasoning_effort": "low",
            "timeout_seconds": 240,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body == {
        "model": "gpt-6-luna",
        "reasoning_effort": "low",
        "speed_mode": "fast",
        "timeout_seconds": 240,
    }

    assert (
        await client.put(
            "/api/memory-study/runners/gpt-5.6-missing/fast",
            json={
                "reasoning_effort": "low",
                "timeout_seconds": 240,
            },
        )
    ).status_code == 404

    listed = await client.get("/api/memory-study/runners")
    assert listed.status_code == 200
    rows = listed.json()
    luna_fast = next(
        row for row in rows
        if row["model"] == "gpt-6-luna" and row["speed_mode"] == "fast"
    )
    assert luna_fast["timeout_seconds"] == 240
    assert len([row for row in rows if row["model"] == "gpt-6-luna"]) == 2

    assert (
        await client.delete("/api/memory-study/runners/gpt-6-luna/fast")
    ).status_code == 204
    assert (
        await client.delete("/api/memory-study/runners/gpt-6-luna/fast")
    ).status_code == 404
    # deleting one speed mode does not remove the other row
    remaining = (await client.get("/api/memory-study/runners")).json()
    assert any(
        row["model"] == "gpt-6-luna" and row["speed_mode"] == "standard"
        for row in remaining
    )


# ----------------------------------------------------------------- site config


async def test_site_config_uses_env_fallback_and_saves(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_MODEL", "default-minilm"
    )
    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_REVISION", "env-revision"
    )
    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_API_BASE", "https://embed.example/v1"
    )
    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_EMBEDDING_API_KEY", "sk-env"
    )

    initial = await client.get("/api/memory-study/site-config")
    assert initial.status_code == 200
    assert initial.json() == {
        "embedding_backend": "openai",
        "embedding_model": "default-minilm",
        "embedding_revision": "env-revision",
        "embedding_api_base": "https://embed.example/v1",
        "embedding_api_key_set": True,
        "embedding_dims": None,
        "revision_pinned": True,
    }

    saved = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "openai",
            "embedding_model": "api-embedding-model",
            "embedding_revision": "v1.2.3-immutable",
            "embedding_api_base": "https://embed.example/v1",
            "embedding_api_key": "sk-test",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["embedding_revision"] == "v1.2.3-immutable"
    assert saved.json()["embedding_backend"] == "openai"
    assert saved.json()["embedding_api_base"] == "https://embed.example/v1"

    after = await client.get("/api/memory-study/site-config")
    assert after.json()["embedding_model"] == "api-embedding-model"


async def test_site_config_cloud_backend_roundtrip(client):
    saved = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "openai",
            "embedding_model": "doubao-embedding-vision",
            "embedding_revision": "doubao-embedding-vision-250615",
            "embedding_api_base": "https://ark.cn-beijing.volces.com/api/v3",
            "embedding_api_key": "sk-test-123",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["embedding_backend"] == "openai"
    assert body["embedding_api_base"] == "https://ark.cn-beijing.volces.com/api/v3"
    # the secret itself is never echoed back
    assert "sk-test-123" not in saved.text
    assert body["embedding_api_key_set"] is True

    # an omitted key keeps the stored secret
    keep = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "openai",
            "embedding_model": "doubao-embedding-vision",
            "embedding_revision": "doubao-embedding-vision-250615",
            "embedding_api_base": "https://ark.cn-beijing.volces.com/api/v3",
        },
    )
    assert keep.status_code == 200
    assert keep.json()["embedding_api_key_set"] is True

    # switching back to a local backend is rejected by the frozen protocol
    cleared = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_revision": "v1.2.3-immutable",
        },
    )
    assert cleared.status_code == 422


async def test_site_config_cloud_backend_validation(client):
    missing_base = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "openai",
            "embedding_model": "doubao-embedding-vision",
            "embedding_revision": "rev-ok",
            "embedding_api_key": "sk-test",
        },
    )
    assert missing_base.status_code == 422

    missing_key = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "openai",
            "embedding_model": "doubao-embedding-vision",
            "embedding_revision": "rev-ok",
            "embedding_api_base": "https://ark.cn-beijing.volces.com/api/v3",
        },
    )
    assert missing_key.status_code == 422

    unknown_backend = await client.put(
        "/api/memory-study/site-config",
        json={
            "embedding_backend": "ollama",
            "embedding_model": "m",
            "embedding_revision": "rev-ok",
        },
    )
    assert unknown_backend.status_code == 422


async def test_site_config_rejects_unpinned_revision(client):
    for blocked in ("", "main", "latest", "unresolved"):
        response = await client.put(
            "/api/memory-study/site-config",
            json={
                "embedding_backend": "openai",
                "embedding_model": "api-embedding-model",
                "embedding_revision": blocked,
                "embedding_api_base": "https://embed.example/v1",
                "embedding_api_key": "sk-test",
            },
        )
        assert response.status_code == 422, blocked


def test_site_config_persists_to_db(db_session):
    service = MemoryStudyService(db_session)
    service.update_site_config(
        {
            "embedding_backend": "openai",
            "embedding_model": "model-a",
            "embedding_revision": "rev-1",
            "embedding_api_base": "https://embed.example/v1",
            "embedding_api_key": "sk-first",
        }
    )
    row = db_session.get(MSSiteConfig, 1)
    assert row is not None
    assert row.embedding_model == "model-a"
    assert row.embedding_revision == "rev-1"
    assert row.embedding_backend == "openai"

    service.update_site_config(
        {
            "embedding_backend": "openai",
            "embedding_model": "doubao-embedding-vision",
            "embedding_revision": "rev-2",
            "embedding_api_base": "https://ark.example.com/api/v3",
            "embedding_api_key": "sk-secret",
            "embedding_dims": 2048,
        }
    )
    row = db_session.get(MSSiteConfig, 1)
    assert row.embedding_backend == "openai"
    assert row.embedding_api_base == "https://ark.example.com/api/v3"
    assert row.embedding_api_key == "sk-secret"
    assert row.embedding_dims == 2048


# ------------------------------------------------------------ protocol config


async def test_protocol_config_exposes_frozen_site_facts(client):
    response = await client.get("/api/memory-study/config")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "saf-memory-framework-v3-r2"
    assert body["tokenizer"] == "o200k_base"
    assert body["codex_subprocess_limit"] == 4
    assert "mem0" in body["official_frameworks"]
    assert "amem" in body["official_frameworks"]


# ---------------------------------------------------------------- delete project


def test_delete_project_running_conflict(db_session):
    _study(db_session, status="running")
    with pytest.raises(Exception) as exc:
        MemoryStudyService(db_session).delete_project("study-1")
    assert exc.type.__name__ == "MemoryStudyDomainError"
    assert "暂停或终止" in str(exc.value)
    assert db_session.get(MSStudy, "study-1") is not None


def test_delete_project_removes_row_and_artifact_dir(db_session, tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(
        config.settings, "MEMORY_STUDY_ARTIFACT_ROOT", str(tmp_path / "artifacts")
    )
    _study(db_session, status="paused")
    artifact_dir = tmp_path / "artifacts" / "study-1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "snapshot.json").write_text("{}")

    MemoryStudyService(db_session).delete_project("study-1")

    assert db_session.get(MSStudy, "study-1") is None
    assert not artifact_dir.exists()


# ------------------------------------------------------- worker runtime override


def _freeze(study: MSStudy, speed_modes: dict[str, str] | None = None) -> None:
    modes = speed_modes or {}
    study.config_json = {
        "models": [
            {
                "model": model,
                "reasoning_effort": "medium",
                "speed_mode": modes.get(model, "standard"),
                "timeout_seconds": 120,
            }
            for model in ("gpt-6-luna",)
        ]
    }


def test_worker_runtime_falls_back_to_frozen_config(db_session):
    study = _study(db_session, status="frozen")
    _freeze(study)
    db_session.commit()
    runtime = _runtime_for_model(db_session, study, "gpt-6-luna")
    assert runtime["timeout_seconds"] == 120
    assert runtime["speed_mode"] == "standard"
    assert runtime["service_tier"] == "default"


def test_worker_runtime_uses_bound_speed_mode(db_session):
    study = _study(db_session, status="frozen")
    _freeze(study, speed_modes={"gpt-6-luna": "fast"})
    db_session.add(
        MSRunnerConfig(
            model="gpt-6-luna",
            speed_mode="fast",
            reasoning_effort="high",
            timeout_seconds=600,
        )
    )
    db_session.add(
        MSRunnerConfig(
            model="gpt-6-luna",
            speed_mode="standard",
            reasoning_effort="medium",
            timeout_seconds=300,
        )
    )
    db_session.commit()
    # the project binding (fast) selects the fast row, not the standard one
    runtime = _runtime_for_model(db_session, study, "gpt-6-luna")
    assert runtime["reasoning_effort"] == "high"
    assert runtime["speed_mode"] == "fast"
    assert runtime["timeout_seconds"] == 600
    assert runtime["service_tier"] == "fast"


def test_worker_runtime_never_uses_other_speed_mode_row(db_session):
    study = _study(db_session, status="frozen")
    # luna is bound to standard; only a fast row exists and must be ignored
    _freeze(study)
    db_session.add(
        MSRunnerConfig(
            model="gpt-6-luna",
            speed_mode="fast",
            reasoning_effort="high",
            timeout_seconds=600,
        )
    )
    db_session.commit()
    runtime = _runtime_for_model(db_session, study, "gpt-6-luna")
    assert runtime["reasoning_effort"] == "medium"
    assert runtime["speed_mode"] == "standard"
    assert runtime["timeout_seconds"] == 120
    assert runtime["service_tier"] == "default"


def test_worker_runtime_bound_fast_missing_row_keeps_frozen(db_session):
    study = _study(db_session, status="frozen")
    # Luna is bound to fast but only a standard row exists; it stays frozen
    _freeze(study, speed_modes={"gpt-6-luna": "fast"})
    db_session.add(
        MSRunnerConfig(
            model="gpt-6-luna",
            speed_mode="standard",
            reasoning_effort="low",
            timeout_seconds=450,
        )
    )
    db_session.commit()
    runtime = _runtime_for_model(db_session, study, "gpt-6-luna")
    assert runtime["reasoning_effort"] == "medium"
    assert runtime["speed_mode"] == "fast"
    assert runtime["timeout_seconds"] == 120
    assert runtime["service_tier"] == "fast"


def test_worker_runtime_uses_standard_row_when_no_fast(db_session):
    study = _study(db_session, status="frozen")
    _freeze(study)
    db_session.add(
        MSRunnerConfig(
            model="gpt-6-luna",
            speed_mode="standard",
            reasoning_effort="low",
            timeout_seconds=450,
        )
    )
    db_session.commit()
    runtime = _runtime_for_model(db_session, study, "gpt-6-luna")
    assert runtime["reasoning_effort"] == "low"
    assert runtime["speed_mode"] == "standard"
    assert runtime["timeout_seconds"] == 450
    assert runtime["service_tier"] == "default"
