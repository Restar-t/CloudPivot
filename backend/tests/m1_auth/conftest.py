"""M1 认证测试夹具：SQLite 内存库 + FakeRedis，依赖注入替换，全链路走 HTTP。

不进 lifespan（省去依赖探测超时）：app.state.redis 由夹具直接挂 FakeRedis。
"""
import pytest
from fastapi.testclient import TestClient
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,  # 内存库单连接共享，跨请求保持数据
        connect_args={"check_same_thread": False},
    )


@pytest.fixture()
def client(engine):
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
    # 不进 lifespan：一是不触发依赖探测超时，二是避免 lifespan 用真实(死)Redis 覆盖 FakeRedis
    app.state.redis = FakeAsyncRedis(decode_responses=True)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def session_factory(engine, client):
    """供测试直接查库断言（login_log / refresh_session 行）。"""
    return async_sessionmaker(engine, expire_on_commit=False)
