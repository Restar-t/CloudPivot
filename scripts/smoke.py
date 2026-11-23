#!/usr/bin/env python3
"""冒烟脚本（M0 建立，随里程碑逐步扩充）。

M0：健康检查通过即绿（/ping 包壳 + /health 三依赖全 up）。
M1 计划：注册 → 登录 → me。
M3 计划：上传 → 下载 → 删除。

用法：
    python scripts/smoke.py                       # 默认 http://127.0.0.1:8000
    NETDISK_BASE_URL=http://127.0.0.1:8000 python scripts/smoke.py

仅用标准库，任何有 Python 的机器都能跑。
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("NETDISK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def fetch(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
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


def main() -> int:
    print(f"冒烟目标: {BASE}")
    failures: list[str] = []
    step_ping(failures)
    step_health(failures)
    print("-" * 50)
    if failures:
        print(f"结果: 红（{len(failures)} 项失败）")
        for f in failures:
            print("  -", f)
        return 1
    print("结果: 绿（M0 冒烟全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
