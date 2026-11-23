"""/health：依赖全 up → 200 ok；任一依赖不可用 → 503 degraded（本地无依赖即此态）。"""
from fastapi.testclient import TestClient


def test_health_reports_all_components(client: TestClient):
    resp = client.get("/api/health")
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert set(data["components"].keys()) == {"mysql", "redis", "minio"}
    assert data["status"] in ("ok", "degraded")
    # 本地测试环境三个依赖均不可达 → 必须是 degraded + 503，且应用不崩溃
    assert data["status"] == "degraded"
    assert resp.status_code == 503
    for value in data["components"].values():
        assert value.startswith("down")


def test_health_detail_contains_error_type(client: TestClient):
    resp = client.get("/api/health")
    data = resp.json()["data"]
    assert data["components"]["mysql"].startswith("down: ")
