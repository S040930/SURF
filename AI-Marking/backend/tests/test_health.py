"""健康检查接口测试。"""


async def test_health_returns_ok(client):
    """GET /api/health 返回 200 且 status=ok。"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


async def test_application_exposes_only_memory_study_routes(client):
    schema = (await client.get("/openapi.json")).json()
    paths = set(schema["paths"])
    assert "/api/health" in paths
    assert "/api/memory-study/runtime" in paths
    assert "/api/memory-study/projects" in paths
    assert "/api/memory-study/projects/{study_id}/start" in paths
    # 旧执行链（r20 在线平台 / r21 / r22 / r23 / 统一实验平台）已退役
    assert not any(
        marker in path
        for path in paths
        for marker in ("/api/r21", "/api/r22", "/api/r23", "/api/experiments")
    )
    assert not any(
        marker in path
        for path in paths
        for marker in ("submissions", "system-config")
    )
