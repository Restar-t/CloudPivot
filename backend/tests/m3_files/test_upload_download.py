"""M3 上传/下载全链路：三层模型落库、物理去重、Range 协议、安全响应头。"""
import hashlib

from sqlalchemy import select

from app.models.file_version import FileVersion
from app.models.quota_usage import QuotaUsage
from app.models.storage_object import StorageObject
from app.models.user_file import UserFile
from tests.conftest import auth


def _upload(api, user, content: bytes, name: str, parent_id: int = 0):
    return api.post(
        "/api/files/upload",
        files={"file": (name, content, "application/octet-stream")},
        data={"parent_id": str(parent_id)},
        headers=auth(user),
    )


def test_upload_creates_three_layer_rows(api, session_factory, db_run, make_user):
    user = make_user("alice")
    content = b"hello netdisk m3"
    resp = _upload(api, user, content, "hello.txt")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["version_no"] == 1
    assert data["sha256"] == hashlib.sha256(content).hexdigest()
    assert data["size"] == len(content)
    file_id = data["id"]

    async def check():
        async with session_factory() as db:
            file = await db.get(UserFile, file_id)
            assert file.size == len(content)
            assert file.current_version_id is not None
            version = await db.get(FileVersion, file.current_version_id)
            assert version.version_no == 1
            assert version.storage_object_id is not None
            obj = await db.get(StorageObject, version.storage_object_id)
            assert obj.sha256 == data["sha256"]
            assert obj.ref_count == 1
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == len(content)

    db_run(check())

    # 内存存储里确实有这份数据
    stored = api.app.state.storage.objects
    assert any(v == content for v in stored.values())


def test_download_full_and_headers(api, make_user):
    user = make_user("alice")
    content = b"0123456789abcdef"
    file_id = _upload(api, user, content, "data.txt").json()["data"]["id"]

    resp = api.get(f"/api/files/{file_id}/download", headers=auth(user))
    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["content-length"] == str(len(content))
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"].startswith("attachment")


def test_download_range_variants(api, make_user):
    user = make_user("alice")
    content = bytes(range(32))  # 0..31
    file_id = _upload(api, user, content, "range.txt").json()["data"]["id"]
    h = auth(user)

    # 0-4：开头段 → 206
    resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": "bytes=0-4"})
    assert resp.status_code == 206
    assert resp.content == content[0:5]
    assert resp.headers["content-range"] == f"bytes 0-4/{len(content)}"

    # 中段 10-19
    resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": "bytes=10-19"})
    assert resp.status_code == 206
    assert resp.content == content[10:20]

    # 尾段 -5
    resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": "bytes=-5"})
    assert resp.status_code == 206
    assert resp.content == content[-5:]

    # 越界 end（clamp 到末尾）
    resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": "bytes=30-999"})
    assert resp.status_code == 206
    assert resp.content == content[30:]

    # 起点越界 → 416 / 2006
    resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": "bytes=32-40"})
    assert resp.status_code == 416
    assert resp.json()["code"] == 2006

    # 语法非法 / 多段 → 忽略，按 200 全量
    for bad in ["bytes=abc", "bytes=0-1,3-4", "items=0-5", "bytes="]:
        resp = api.get(f"/api/files/{file_id}/download", headers={**h, "Range": bad})
        assert resp.status_code == 200, bad
        assert resp.content == content

    # If-Range 不匹配 → 200 全量；匹配 → 206
    etag = f'"{hashlib.sha256(content).hexdigest()}"'
    resp = api.get(f"/api/files/{file_id}/download",
                   headers={**h, "Range": "bytes=0-3", "If-Range": '"stale-etag"'})
    assert resp.status_code == 200
    resp = api.get(f"/api/files/{file_id}/download",
                   headers={**h, "Range": "bytes=0-3", "If-Range": etag})
    assert resp.status_code == 206
    assert resp.content == content[0:4]


def test_physical_dedup_same_content(api, session_factory, db_run, make_user):
    """同内容两次上传 → 一个物理对象 ref_count=2，两个逻辑文件各计配额（无秒传：字节都传了）。"""
    user = make_user("alice")
    content = b"duplicate-content-123"
    r1 = _upload(api, user, content, "a.txt").json()["data"]
    r2 = _upload(api, user, content, "b.txt").json()["data"]
    assert r1["storage_object_id"] == r2["storage_object_id"]

    async def check():
        async with session_factory() as db:
            obj = await db.get(StorageObject, r1["storage_object_id"])
            assert obj.ref_count == 2
            versions = (await db.execute(select(FileVersion))).scalars().all()
            assert len(versions) == 2
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == 2 * len(content)  # 逻辑配额双计

    db_run(check())

    # 两份都可完整下载
    for r in (r1, r2):
        resp = api.get(f"/api/files/{r['id']}/download", headers=auth(user))
        assert resp.content == content


def test_upload_validation_errors(api, make_user):
    user = make_user("alice")
    # 空文件
    resp = _upload(api, user, b"", "empty.txt")
    assert resp.status_code == 422
    assert resp.json()["code"] == 9001
    # 白名单外扩展名 → 415 / 3002
    resp = _upload(api, user, b"malware", "evil.exe")
    assert resp.status_code == 415
    assert resp.json()["code"] == 3002
    # 非法文件名 → 400 / 2003
    resp = _upload(api, user, b"x", "../evil.txt")
    assert resp.status_code == 400
    assert resp.json()["code"] == 2003
    # 超过 1KiB 上限 → 413 / 3001
    resp = _upload(api, user, b"x" * 2048, "big.txt")
    assert resp.status_code == 413
    assert resp.json()["code"] == 3001
    # 同名冲突 → 409 / 2002
    _upload(api, user, b"first", "dup.txt")
    resp = _upload(api, user, b"second", "dup.txt")
    assert resp.status_code == 409
    assert resp.json()["code"] == 2002


def test_cross_user_isolation_on_files(api, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    file_id = _upload(api, alice, b"private-data", "secret.txt").json()["data"]["id"]

    resp = api.get(f"/api/files/{file_id}/download", headers=auth(bob))
    assert resp.status_code == 404
    assert resp.json()["code"] == 2001
    resp = api.delete(f"/api/files/{file_id}", headers=auth(bob))
    assert resp.status_code == 404

    # B 上传同名文件到自己的根：与 A 互不可见
    resp = _upload(api, bob, b"bob-file", "secret.txt")
    assert resp.status_code == 200
    listing = api.get("/api/folders/0/children", headers=auth(bob)).json()["data"]
    assert listing["total"] == 1


def test_trash_and_quota_untouched(api, session_factory, db_run, make_user):
    user = make_user("alice")
    file_id = _upload(api, user, b"to-be-trashed", "trash.txt").json()["data"]["id"]

    async def used_before():
        async with session_factory() as db:
            return (await db.execute(select(QuotaUsage))).scalar_one().used_bytes

    resp = api.delete(f"/api/files/{file_id}", headers=auth(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["trashed"] == 1

    async def check():
        async with session_factory() as db:
            file = await db.get(UserFile, file_id)
            assert file.status == "TRASH"
            assert file.deleted_at is not None
            assert file.generation == 1
            assert file.alive is None

    db_run(check())
    assert db_run(used_before()) == 13  # 回收站占配额

    # 回收站文件不可下载
    resp = api.get(f"/api/files/{file_id}/download", headers=auth(user))
    assert resp.status_code == 404

    # 同名可再建（活跃唯一键放行软删行）
    resp = _upload(api, user, b"recreate", "trash.txt")
    assert resp.status_code == 200


def test_trash_folder_recursive(api, session_factory, db_run, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "dir"},
                      headers=auth(user)).json()["data"]
    sub = api.post("/api/folders", json={"parent_id": folder["id"], "name": "sub"},
                   headers=auth(user)).json()["data"]
    f1 = _upload(api, user, b"1", "f1.txt", parent_id=folder["id"]).json()["data"]
    f2 = _upload(api, user, b"2", "f2.txt", parent_id=sub["id"]).json()["data"]

    resp = api.delete(f"/api/files/{folder['id']}", headers=auth(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["trashed"] == 4  # dir + sub + f1 + f2 整棵子树

    async def check():
        async with session_factory() as db:
            for fid in (folder["id"], sub["id"], f1["id"], f2["id"]):
                row = await db.get(UserFile, fid)
                assert row.status == "TRASH", fid
                assert row.deleted_at is not None

    db_run(check())

    # 子文件不可再下载
    assert api.get(f"/api/files/{f2['id']}/download", headers=auth(user)).status_code == 404


def _set_quota(db_run, session_factory, username: str, quota_bytes: int) -> None:
    """直接改库调整某用户额度（模拟管理员调额），在应用事件循环内执行。"""
    from sqlalchemy import update as _update
    from app.models.user import User as _User

    async def _go():
        async with session_factory() as db:
            await db.execute(
                _update(_User).where(_User.username == username).values(quota_bytes=quota_bytes)
            )
            await db.commit()

    db_run(_go())


def test_quota_endpoint_and_over_limit(api, session_factory, db_run, make_user):
    user = make_user("alice")
    _upload(api, user, b"aaaa", "q1.txt")  # 已用 4

    resp = api.get("/api/users/me/quota", headers=auth(user))
    data = resp.json()["data"]
    assert data["used_bytes"] == 4
    assert data["quota_bytes"] == 10 * 1024**3

    # 额度调到 10：边界用例
    _set_quota(db_run, session_factory, "alice", 10)
    assert api.get("/api/users/me/quota", headers=auth(user)).json()["data"] == {
        "quota_bytes": 10, "used_bytes": 4, "remaining_bytes": 6,
    }

    # ① 6 字节：4+6 == 10，刚好等于额度 → 允许
    resp = _upload(api, user, b"123456", "q2.txt")
    assert resp.status_code == 200, resp.text
    assert api.get("/api/users/me/quota", headers=auth(user)).json()["data"]["used_bytes"] == 10

    # ② 再传 1 字节：10+1 > 10 → 507 / 5001，响应给出明确用量
    resp = _upload(api, user, b"x", "q3.txt")
    assert resp.status_code == 507, resp.text
    assert resp.json()["code"] == 5001
    assert resp.json()["data"] == {"quota_bytes": 10, "used_bytes": 10, "need_bytes": 1}

    async def check_no_change():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == 10  # 用量未被污染
            files = (
                await db.execute(select(UserFile).where(UserFile.name == "q3.txt"))
            ).scalars().all()
            assert files == []  # 无脏元数据

    db_run(check_no_change())
    # 被拒的字节已写入对象存储成为孤儿（DB 零变更）——由 M7 GC 回收，此处如实记录该边界
    assert any(v == b"x" for v in api.app.state.storage.objects.values())

    # ③ 额度恢复后，同样的 1 字节可以上传
    _set_quota(db_run, session_factory, "alice", 10 * 1024**3)
    resp = _upload(api, user, b"x", "q3.txt")
    assert resp.status_code == 200, resp.text
    assert api.get("/api/users/me/quota", headers=auth(user)).json()["data"]["used_bytes"] == 11
