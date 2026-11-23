"""统一响应包 / 错误码 / 全局异常处理器的行为验证（不依赖外部服务）。"""
from fastapi.testclient import TestClient


def test_ping_envelope(client: TestClient):
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"] == "pong"
    assert resp.headers.get("x-trace-id")  # trace 中间件回传


def test_unknown_route_returns_envelope_404(client: TestClient):
    resp = client.get("/api/definitely-not-exists")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert body["code"] == 9002  # ROUTE_NOT_FOUND


def test_method_not_allowed(client: TestClient):
    resp = client.delete("/api/ping")
    assert resp.status_code == 405
    assert resp.json()["code"] == 9003


def test_validation_error_envelope(client: TestClient, envelope_router):
    resp = client.get("/api/__test/validate")  # 缺必填参数 q
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 9001
    assert body["data"][0]["loc"].startswith("query")


def test_validation_ok(client: TestClient, envelope_router):
    resp = client.get("/api/__test/validate", params={"q": 42})
    assert resp.status_code == 200
    assert resp.json() == {"code": 0, "message": "ok", "data": 42}


def test_biz_error_envelope(client: TestClient, envelope_router):
    resp = client.get("/api/__test/biz-error")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 2001
    assert body["message"] == "demo not found"


def test_raw_return_autowrapped(client: TestClient, envelope_router):
    """路由返回裸 dict 时由 ApiRoute 自动包壳。"""
    from fastapi import APIRouter

    from app.core.response import ApiRoute

    r = APIRouter(route_class=ApiRoute)

    @r.get("/__test/raw")
    async def _raw():
        return {"hello": "world"}

    app = client.app
    app.include_router(r, prefix="/api")
    try:
        resp = client.get("/api/__test/raw")
        assert resp.json() == {"code": 0, "message": "ok", "data": {"hello": "world"}}
    finally:
        app.router.routes = [
            rt for rt in app.router.routes if "__test" not in getattr(rt, "path", "")
        ]
