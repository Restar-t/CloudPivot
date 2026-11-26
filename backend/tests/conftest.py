"""测试夹具。

- 测试环境绝不触碰真实外部服务：强制覆盖连接参数为死端口，并禁用 backend/.env
  （否则本机若恰有 Redis/MinIO 在跑，测试会被真实服务干扰）。
- `api` 夹具：SQLite 内存库 + FakeRedis + MemoryStorage + 依赖注入替换。
  数据库协程必须经 `db_run()` 在应用自身事件循环里执行（不可用 asyncio.run，
  会与 TestClient 内部循环共用连接导致事务状态错乱）。
- `client` 夹具（M0 用）：进 lifespan，验证启动期容错与 /health。
"""
import os

# 必须先于导入 app：强制覆盖（非 setdefault），不受已有环境变量/.env 影响
_TEST_ENV = {
    "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "53306",     # 无人监听 → 快速失败
    "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "56379",
    "MINIO_ENDPOINT": "127.0.0.1:59000", "MINIO_CALL_TIMEOUT": "1",
    "BUCKET_INIT_RETRIES": "1", "BUCKET_INIT_DELAY_SECONDS": "0",
    "PASSWORD_HASH_ROUNDS": "4",      # 低轮数 bcrypt：只影响哈希成本，不影响逻辑
    "UPLOAD_MAX_FILE_SIZE": "1024",   # 收紧上限：超限用例无需构造大文件
    "UPLOAD_CHUNK_SIZE": "64",        # 64B 分片：小文件即可测多分片/断点续传
    "CANDIDATE_GRACE_HOURS": "0",     # GC 宽限期 0：孤儿清理用例立即可收集
    "ENV_FILE": "",                   # 不加载 backend/.env（见 config.py 的 env_file 取值）
}
os.environ.update(_TEST_ENV)

import pytest
from fastapi.testclient import TestClient
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from tests.memory_storage import MemoryStorage


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
    """业务测试客户端：SQLite + FakeRedis + MemoryStorage + 依赖注入替换，全链路走 HTTP。

    关键设计——进入 `with TestClient` 但**不跑真实 lifespan**：
    - 需要 `with`：才能拿到 portal，让数据库协程与应用请求跑在同一个事件循环
      （否则 StaticPool 单连接被两个循环共用，事务状态错乱）；
    - 不能跑真实 lifespan：它会连真实 Redis/MinIO、覆盖测试替身，并在退出时清理 state，
      污染后续用例。故临时把 lifespan_context 换成空实现。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    async def override_get_db():
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.redis = FakeAsyncRedis(decode_responses=True)
    app.state.storage = MemoryStorage()  # 文件字节进内存，测试不依赖真实 MinIO

    with TestClient(app) as client:
        async def create_all():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        client.portal.call(create_all)  # 建表也在应用的事件循环里完成
        yield client

    app.router.lifespan_context = original_lifespan
    app.dependency_overrides.clear()


@pytest.fixture()
def db_run(api):
    """在应用自身的事件循环里执行数据库协程：`db_run(some_async_fn(args))`。

    不能用 asyncio.run()：它会另起事件循环，与 TestClient 内部循环共用同一条
    StaticPool 连接，导致事务状态错乱（表现为"改了库但请求看不到"）。

    注意 anyio 的 BlockingPortal.call(func, *args) 要求传入"可调用对象"而非协程，
    故此处把协程对象包一层再提交（否则报 'coroutine' object is not callable）。
    """
    def _run(coro):
        return api.portal.call(lambda: coro)
    return _run


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
