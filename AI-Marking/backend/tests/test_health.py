"""健康检查接口测试。"""


async def test_health_returns_ok(client):
    """GET /api/health 返回 200 且 status=ok。"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


async def test_application_exposes_only_experiment_routes(client):
    schema = (await client.get("/openapi.json")).json()
    paths = set(schema["paths"])
    assert "/api/health" in paths
    assert not any("/api/legacy" in path for path in paths)
    assert "/api/r20/projects" in paths
    assert "/api/r20/projects/{project_id}" in paths
    assert "/api/r20/prompt-versions" in paths
    assert "/api/r20/model-configs" in paths
    assert "/api/r23/data-status" in paths
    assert "/api/r23/runtime" in paths
    assert "/api/r23/projects" in paths
    assert "/api/r23/projects/{project_id}/analysis" in paths
    assert "/api/r23/projects/{project_id}/repeat" in paths
    assert "/api/r23/runner-configs/{config_id}/freeze" not in paths
    assert "/api/r23/rubrics/{rubric_id}/freeze" not in paths
    assert "/api/r23/projects/{project_id}/freeze" not in paths
    assert not any("experiment-stages" in path for path in paths)
    assert not any("/api/model-profiles" in path for path in paths)
    assert not any(
        marker in path
        for path in paths
        for marker in ("submissions", "/questions", "system-config")
    )


async def test_r23_runtime_reports_shared_mcp_connection(client):
    response = await client.get("/api/r23/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runner_online"] is False
    assert payload["reason"] == "no MCP runner is connected"
