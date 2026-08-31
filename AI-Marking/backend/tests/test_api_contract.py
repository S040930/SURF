"""统一错误契约与 API Key 鉴权测试。"""

from __future__ import annotations


async def test_validation_error_returns_string_detail(client):
    """校验 422 的 detail 必须是字符串（前端按字符串解析）。"""
    for path in ("/api/r20/projects",):
        resp = await client.post(path, json={})
        assert resp.status_code == 405, path


async def test_r20_write_endpoints_are_closed(client):
    """r20 is an archive: mutation routes must be unavailable."""
    resp = await client.post(
        "/api/r20/prompt-versions",
        json={
            "name": "broken",
            "scoring": "t",
            "crm_update": "t",
            "arm_update": "t",
        },
    )
    assert resp.status_code == 405


async def test_failures_list_is_blinded(client, db_session):
    """失败列表只暴露元数据与原因，不暴露输入或教师数据。"""
    from uuid import uuid4

    from app.models.r20 import R20Call, R20Project

    project = R20Project(
        id=str(uuid4()),
        name="failures-blind",
        kind="pilot_run",
        status="running",
        model_config_ids_json=["a", "b"],
        model_configs_json=[],
        model_configs_sha256="m" * 64,
        prompt_version_id="p",
        prompt_version_name="p",
        prompt_templates_json={},
        prompt_version_sha256="p" * 64,
        manifest_json={"expected_calls": 100},
        manifest_sha256="x" * 64,
        data_sha256="d" * 64,
        analysis_code_sha256="a" * 64,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(
        R20Call(
            project_id=project.id,
            model_id="m",
            kind="test_score",
            status="failed_terminal",
            condition="nm",
            question_id="q",
            answer_id="a",
            group_id="g",
            trajectory=1,
            history_count=40,
            repeat=1,
            input_json={"teacher_feedback": "secret"},
            failure_reason="network error",
        )
    )
    db_session.commit()
    resp = await client.get(f"/api/r20/projects/{project.id}/failures")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["failure_reason"] == "network error"
    assert "input_json" not in item
    assert "teacher_score" not in item
    assert "teacher_feedback" not in item
    assert "output_json" not in item


async def test_retry_guards_are_enforced(client, db_session):
    """重试接口拒绝不存在调用与仅接受 failed_terminal 调用。"""
    from uuid import uuid4

    from app.models.r20 import R20Call, R20Project, R20Stream

    project = R20Project(
        id=str(uuid4()),
        name="retry-guards",
        kind="pilot_run",
        status="running",
        model_config_ids_json=["a", "b"],
        model_configs_json=[],
        model_configs_sha256="m" * 64,
        prompt_version_id="p",
        prompt_version_name="p",
        prompt_templates_json={},
        prompt_version_sha256="p" * 64,
        manifest_json={"expected_calls": 100},
        manifest_sha256="x" * 64,
        data_sha256="d" * 64,
        analysis_code_sha256="a" * 64,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(
        R20Stream(
            project_id=project.id,
            model_id="m",
            question_id="q",
            condition="nm",
            trajectory=1,
            order_rank=0,
            status="pending",
        )
    )
    call = R20Call(
        project_id=project.id,
        model_id="m",
        kind="test_score",
        status="failed_terminal",
        condition="nm",
        question_id="q",
        answer_id="a",
        group_id="g",
        trajectory=1,
        history_count=40,
        repeat=1,
        input_json={},
        failure_reason="config",
    )
    db_session.add(call)
    db_session.commit()
    missing = await client.post(f"/api/r20/projects/{project.id}/calls/999999/retry")
    assert missing.status_code == 404
    retried = await client.post(f"/api/r20/projects/{project.id}/calls/{call.id}/retry")
    assert retried.status_code == 404


async def test_project_controls_endpoints(client, db_session):
    """暂停、恢复、删除接口的契约与守卫。"""
    from uuid import uuid4

    from app.models.r20 import R20Project

    project = R20Project(
        id=str(uuid4()),
        name="controls",
        kind="pilot_run",
        status="running",
        model_config_ids_json=["a", "b"],
        model_configs_json=[],
        model_configs_sha256="m" * 64,
        prompt_version_id="p",
        prompt_version_name="p",
        prompt_templates_json={},
        prompt_version_sha256="p" * 64,
        manifest_json={"expected_calls": 100},
        manifest_sha256="x" * 64,
        data_sha256="d" * 64,
        analysis_code_sha256="a" * 64,
    )
    db_session.add(project)
    db_session.commit()

    paused = await client.post(f"/api/r20/projects/{project.id}/pause")
    assert paused.status_code == 404
    assert (
        await client.post(f"/api/r20/projects/{project.id}/resume")
    ).status_code == 404
    assert (await client.delete(f"/api/r20/projects/{project.id}")).status_code == 405
