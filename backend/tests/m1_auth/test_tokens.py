"""令牌生命周期：me 鉴权 / 刷新轮换 + 重用检测 / 退出黑名单 / 改密失效。"""
import asyncio
import time

from sqlalchemy import select

from app.models.refresh_session import RefreshSession


def _register_and_login(client, username="alice"):
    client.post("/api/auth/register", json={"username": username, "password": "alicepass123"})
    resp = client.post("/api/auth/login", json={"username": username, "password": "alicepass123"})
    return resp.json()["data"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- /users/me ----------
def test_me_ok(client):
    tokens = _register_and_login(client)
    resp = client.get("/api/users/me", headers=_auth(tokens["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["data"]["username"] == "alice"


def test_me_without_token(client):
    resp = client.get("/api/users/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == 1001


def test_me_garbage_token(client):
    resp = client.get("/api/users/me", headers=_auth("not-a-jwt"))
    assert resp.status_code == 401
    assert resp.json()["code"] == 1001


def test_me_tampered_signature(client):
    tokens = _register_and_login(client)
    tampered = tokens["access_token"][:-3] + ("AAA" if not tokens["access_token"].endswith("AAA") else "BBB")
    resp = client.get("/api/users/me", headers=_auth(tampered))
    assert resp.status_code == 401
    assert resp.json()["code"] == 1001


def test_me_expired_token(client, monkeypatch):
    """过期 access → 1002（缩短 TTL 造过期令牌）。"""
    from app.core import security
    from app.core.errors import BizError, ErrorCode

    tokens = _register_and_login(client)
    # 直接签一个已过期的令牌
    import jwt as pyjwt
    from app.core.config import get_settings

    settings = get_settings()
    now = int(time.time())
    expired = pyjwt.encode(
        {"sub": "1", "jti": "x", "iat": now - 3600, "exp": now - 60, "type": "access"},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    resp = client.get("/api/users/me", headers=_auth(expired))
    assert resp.status_code == 401
    assert resp.json()["code"] == 1002


# ---------- 刷新轮换与重用检测 ----------
def test_refresh_rotation(client):
    tokens = _register_and_login(client)
    old_refresh = tokens["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    new_pair = resp.json()["data"]
    assert new_pair["refresh_token"] != old_refresh

    # 新 access 可用
    resp = client.get("/api/users/me", headers=_auth(new_pair["access_token"]))
    assert resp.status_code == 200

    # 旧 refresh 重用 → 1003，且整个族被撤销（新 refresh 随之失效）
    resp = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003
    resp = client.post("/api/auth/refresh", json={"refresh_token": new_pair["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003


def test_refresh_unknown_token(client):
    resp = client.post("/api/auth/refresh", json={"refresh_token": "x" * 48})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1001


# ---------- 退出 ----------
def test_logout_revokes_session(client):
    tokens = _register_and_login(client)
    resp = client.post(
        "/api/auth/logout",
        headers=_auth(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200

    # 策略断言：退出后旧 access 立即失效（jti 黑名单），旧 refresh 也失效
    resp = client.get("/api/users/me", headers=_auth(tokens["access_token"]))
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003


# ---------- 改密 ----------
def test_change_password_invalidates_other_sessions(client):
    tokens_a = _register_and_login(client)
    tokens_b = _register_and_login(client)  # 第二设备

    time.sleep(1.05)  # 保证 B 的 access iat 严格早于改密时刻（秒级粒度）

    resp = client.put(
        "/api/auth/password",
        headers={**_auth(tokens_a["access_token"]), "X-Refresh-Token": tokens_a["refresh_token"]},
        json={"old_password": "alicepass123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 200
    rotated = resp.json()["data"]

    # A 拿到轮换后的新对，本设备保持登录
    resp = client.get("/api/users/me", headers=_auth(rotated["access_token"]))
    assert resp.status_code == 200

    # B 的旧 access（改密前签发）失效
    resp = client.get("/api/users/me", headers=_auth(tokens_b["access_token"]))
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003
    # B 的 refresh 也被撤销
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens_b["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["code"] == 1003

    # 旧密码登录失败，新密码登录成功
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "alicepass123"})
    assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "newpassword456"})
    assert resp.status_code == 200


def test_change_password_wrong_old(client):
    tokens = _register_and_login(client)
    resp = client.put(
        "/api/auth/password",
        headers=_auth(tokens["access_token"]),
        json={"old_password": "wrongoldpwd1", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 1004


def test_refresh_session_rows_track_rotation(client, session_factory):
    tokens = _register_and_login(client)
    pair = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()["data"]
    client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})

    async def check():
        async with session_factory() as db:
            rows = (await db.execute(select(RefreshSession))).scalars().all()
            assert len(rows) == 3  # 登录1 + 轮换2
            active = [r for r in rows if r.revoked_at is None]
            assert len(active) == 1  # 始终只有族内最新行有效
            rotated = [r for r in rows if r.revoked_reason == "rotated"]
            assert len(rotated) == 2

    asyncio.run(check())
