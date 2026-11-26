#!/usr/bin/env python3
"""冒烟脚本（M0 建立，随里程碑逐步扩充）。

M0：健康检查通过即绿（/ping 包壳 + /health 三依赖全 up）。
M1：注册（幂等）→ 登录 → me。
M3：上传 → 下载（校验 SHA-256）→ 删除进回收站 → 确认不可下载。

用法：
    python scripts/smoke.py                       # 默认 http://127.0.0.1:8000
    NETDISK_BASE_URL=http://127.0.0.1:8000 python scripts/smoke.py

仅用标准库，任何有 Python 的机器都能跑。
"""
import hashlib
import io
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("NETDISK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

SMOKE_USERNAME = "smoke_user"
SMOKE_PASSWORD = "Smoke12345678"


def fetch(path: str, method: str = "GET", payload: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要读出响应包
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


def step_ping(failures: list[str]) -> None:
    status, body = fetch("/api/ping")
    if status != 200 or body.get("code") != 0 or body.get("data") != "pong":
        failures.append(f"/api/ping 异常: HTTP {status}, body={body}")
        print("[FAIL] /api/ping")
        return
    print("[OK]   /api/ping -> 响应包正常 (code=0)")


def step_health(failures: list[str]) -> None:
    status, body = fetch("/api/health")
    data = body.get("data") or {}
    components = data.get("components") or {}
    if status != 200:
        failures.append(f"/api/health 依赖未就绪: HTTP {status}, components={components}")
        print(f"[FAIL] /api/health -> HTTP {status} ({components})")
        return
    if body.get("code") != 0 or data.get("status") != "ok":
        failures.append(f"/api/health 响应异常: body={body}")
        print(f"[FAIL] /api/health -> body={body}")
        return
    print(f"[OK]   /api/health -> {', '.join(f'{k}={v}' for k, v in components.items())}")


def step_auth(failures: list[str]) -> dict | None:
    """注册（幂等）→ 登录 → me；成功返回 access_token，失败返回 None。"""
    status, body = fetch(
        "/api/auth/register", method="POST", payload={"username": SMOKE_USERNAME, "password": SMOKE_PASSWORD}
    )
    if not (status == 200 and body.get("code") == 0) and body.get("code") != 1006:
        failures.append(f"注册失败: HTTP {status}, body={body}")
        print(f"[FAIL] /api/auth/register -> HTTP {status}, {body.get('code')}")
        return None

    status, body = fetch(
        "/api/auth/login", method="POST", payload={"username": SMOKE_USERNAME, "password": SMOKE_PASSWORD}
    )
    data = body.get("data") or {}
    if status != 200 or body.get("code") != 0 or not data.get("access_token"):
        failures.append(f"登录失败: HTTP {status}, body={body}")
        print(f"[FAIL] /api/auth/login -> HTTP {status}, {body.get('code')}")
        return None
    print(f"[OK]   /api/auth/login -> 取得 token (expires_in={data.get('expires_in')}s)")

    status, body = fetch("/api/users/me", token=data["access_token"])
    me = body.get("data") or {}
    if status != 200 or body.get("code") != 0 or me.get("username") != SMOKE_USERNAME:
        failures.append(f"me 异常: HTTP {status}, body={body}")
        print(f"[FAIL] /api/users/me -> HTTP {status}, {body.get('code')}")
        return None
    print(f"[OK]   /api/users/me -> {me.get('username')} (quota={me.get('quota_bytes')})")
    return data["access_token"]


def _multipart_upload(token: str, name: str, content: bytes) -> tuple[int, dict]:
    """手写 multipart/form-data（仅标准库）。"""
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="parent_id"\r\n\r\n0\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/files/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


def _download(token: str, file_id: int) -> tuple[int, bytes]:
    req = urllib.request.Request(
        BASE + f"/api/files/{file_id}/download", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def step_files(failures: list[str], token: str) -> None:
    content = f"smoke {uuid.uuid4().hex} 内容校验样例".encode("utf-8")
    name = f"smoke_{uuid.uuid4().hex[:8]}.txt"
    expect_sha = hashlib.sha256(content).hexdigest()

    # 上传
    status, body = _multipart_upload(token, name, content)
    data = body.get("data") or {}
    if status != 200 or body.get("code") != 0:
        failures.append(f"上传失败: HTTP {status}, body={body}")
        print(f"[FAIL] /api/files/upload -> HTTP {status}, {body.get('code')}")
        return
    if data.get("sha256") != expect_sha:
        failures.append(f"上传 sha256 不符: 期望 {expect_sha}, 实际 {data.get('sha256')}")
        print("[FAIL] 上传 sha256 服务端核验不符")
        return
    file_id = data["id"]
    print(f"[OK]   /api/files/upload -> {name} ({len(content)}B, sha256 匹配, v{data.get('version_no')})")

    # 下载 + 完整性校验
    status, got = _download(token, file_id)
    if status != 200 or hashlib.sha256(got).hexdigest() != expect_sha:
        failures.append(f"下载内容校验失败: HTTP {status}, 期望sha={expect_sha}, 实际={hashlib.sha256(got).hexdigest()}")
        print(f"[FAIL] /api/files/{file_id}/download -> 内容不一致")
        return
    print(f"[OK]   /api/files/{file_id}/download -> 内容 SHA-256 一致")

    # 删除 → 回收站 → 不可再下载
    status, body = fetch(f"/api/files/{file_id}", method="DELETE", token=token)
    if status != 200 or body.get("code") != 0 or body["data"].get("trashed") != 1:
        failures.append(f"删除失败: HTTP {status}, body={body}")
        print(f"[FAIL] DELETE /api/files/{file_id} -> HTTP {status}, {body.get('code')}")
        return
    status, _ = _download(token, file_id)
    if status != 404:
        failures.append(f"删除后仍可下载: HTTP {status}")
        print(f"[FAIL] 删除后下载应 404，实际 HTTP {status}")
        return
    print(f"[OK]   DELETE /api/files/{file_id} -> 进回收站，下载返回 404")


def main() -> int:
    print(f"冒烟目标: {BASE}")
    failures: list[str] = []
    step_ping(failures)
    step_health(failures)
    token = step_auth(failures)
    if token:
        step_files(failures, token)
    print("-" * 50)
    if failures:
        print(f"结果: 红（{len(failures)} 项失败）")
        for f in failures:
            print("  -", f)
        return 1
    print("结果: 绿（冒烟全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
