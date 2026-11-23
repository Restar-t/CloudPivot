"""测试夹具。

- 本地无 MySQL/Redis/MinIO：环境变量指向死端口，应用必须能启动并报告 degraded。
- `api` 夹具：SQLite 内存库 + FakeRedis + 依赖注入替换（M1 起业务测试共用），
  不进 lifespan（避免依赖探测超时与真实 Redis 覆盖 FakeRedis）。
- `client` 夹具（M0 用）：进 lifespan，验证启动期容错与 /health。
"""
import os

# 在导入 app 之前生效，避免真实连到本机 3306 上的无关 MySQL
os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "53306")  # 无人监听 → 检查快速失败
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "56379")
os.environ.setdefault("MINIO_ENDPOINT", "127.0.0.1:59000")
os.environ.setdefault("MINIO_CALL_TIMEOUT", "1")
os.environ.setdefault("BUCKET_INIT_RETRIES", "1")
os.environ.setdefault("BUCKET_INIT_DELAY_SECONDS", "0")
# 测试用低轮数 bcrypt（安全参数只影响哈希成本，不影响逻辑）
os.environ.setdefault("PASSWORD_HASH_ROUNDS", "4")

import pytest
from fastapi.testclient import TestClient
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def client():
    # with 语法触发 lifespan（容器初始化路径）；模块级共享，避免每个用例重复启动
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def envelope_router():
    """临时路由：验证响应包壳 / 参数校验 / 业务异常三条路径，不污染生产路由。"""
    from fastapi import APIRouter

    from app.core.errors import BizError, ErrorCode
    from app.core.response import ApiRoute, ok

    r = APIRouter(route_class=ApiRoute)

    @r.get("/__test/validate")
    async def _validate(q: int):
        return ok(q)

    @r.get("/__test/biz-error")
    async def _biz_error():
        raise BizError(ErrorCode.NOT_FOUND, message="demo not found")

    app.include_router(r, prefix="/api")
    yield r
    app.router.routes = [rt for rt in app.router.routes if "__test" not in getattr(rt, "path", "")]


@pytest.fixture()
def engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,  # 内存库单连接共享，跨请求保持数据
        connect_args={"check_same_thread": False},
    )


@pytest.fixture()
def api(engine):
    """业务测试客户端：SQLite + FakeRedis，依赖注入替换，全链路走 HTTP。"""
    ready = False

    async def override_get_db():
        nonlocal ready
        if not ready:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            ready = True
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.redis = FakeAsyncRedis(decode_responses=True)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def session_factory(engine):
    """供测试直接查库断言（login_log / refresh_session / user_file 行）。"""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture()
def make_user(api):
    """注册并登录一个测试用户 → {username, password, access_token, refresh_token, user}。"""

    def _make(username: str, password: str = "password123"):
        resp = api.post("/api/auth/register", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        resp = api.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        return {
            "username": username,
            "password": password,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "user": data["user"],
        }

    return _make


def auth(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['access_token']}"}
