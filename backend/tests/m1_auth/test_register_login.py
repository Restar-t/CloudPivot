"""注册与登录主流程 + 爆破锁定。"""

from sqlalchemy import func, select

from app.models.login_log import RESULT_FAILED, RESULT_LOCKED, RESULT_SUCCESS, LoginLog
from app.models.user import User


def test_register_success(client):
    resp = client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["username"] == "alice"
    assert body["data"]["quota_bytes"] == 10 * 1024**3
    assert "password" not in str(body).lower().replace("password_hash", "")


def test_register_duplicate(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})
    resp = client.post("/api/auth/register", json={"username": "alice", "password": "otherpass456"})
    assert resp.status_code == 409
    assert resp.json()["code"] == 1006


def test_register_invalid_input(client):
    resp = client.post("/api/auth/register", json={"username": "非法名", "password": "alicepass123"})
    assert resp.status_code == 422
    assert resp.json()["code"] == 9001
    resp = client.post("/api/auth/register", json={"username": "bob", "password": "short"})
    assert resp.status_code == 422


def test_login_success(client, session_factory, db_run):
    client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "alicepass123"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["expires_in"] == 15 * 60
    assert data["user"]["username"] == "alice"

    async def check_log():
        async with session_factory() as db:
            logs = (await db.execute(select(LoginLog).order_by(LoginLog.id))).scalars().all()
            assert [l.result for l in logs] == [RESULT_SUCCESS]
            assert logs[0].username == "alice"

    db_run(check_log())


def test_login_wrong_password(client, session_factory, db_run):
    client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass999"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1004

    async def check_log():
        async with session_factory() as db:
            n = await db.scalar(select(func.count()).where(LoginLog.result == RESULT_FAILED))
            assert n == 1

    db_run(check_log())


def test_login_nonexistent_user_same_error(client):
    """不存在的用户与密码错误返回同一错误码（防枚举）。"""
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever123"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1004


def test_login_brute_force_lockout(client):
    """阈值 5 次/窗口内失败 → 锁定；即使密码正确也 429。"""
    client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "badbadbad01"})
        assert resp.status_code == 401
    # 第 6 次：密码正确但仍被锁
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "alicepass123"})
    assert resp.status_code == 429
    assert resp.json()["code"] == 1005
    # 锁定不影响其他 IP（计数按 账号+IP 组合）
    resp = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alicepass123"},
        headers={"X-Forwarded-For": "10.0.0.9"},
    )
    assert resp.status_code == 200


def test_user_row_never_stores_plain_password(client, session_factory, db_run):
    client.post("/api/auth/register", json={"username": "alice", "password": "alicepass123"})

    async def check():
        async with session_factory() as db:
            user = (await db.execute(select(User).where(User.username == "alice"))).scalar_one()
            assert user.password_hash != "alicepass123"
            assert user.password_hash.startswith("$2b$")

    db_run(check())
