"""M0 单测夹具。

本地无 MySQL/Redis/MinIO 时：把外部依赖指向不存在的地址，
应用必须仍能启动（lifespan 容错），/health 报 degraded —— 这本身就是 M0 的
"系统始终可运行 + 依赖故障不拖垮服务"的验证点。
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

from app.main import app


@pytest.fixture(scope="module")
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
