"""M4 回收站测试：列表/还原(重名409+建议名)/彻底删除(引用释放+配额释放)/清空。"""
from sqlalchemy import select

from app.models.file_version import FileVersion
from app.models.quota_usage import QuotaUsage
from app.models.storage_object import StorageObject
from app.models.user_file import UserFile
from tests.conftest import auth


def _upload(api, user, content: bytes, name: str, parent_id: int = 0):
    return api.post(
        "/api/files/upload",
        files={"file": (name, content, "text/plain")},
        data={"parent_id": str(parent_id)},
        headers=auth(user),
    )


def test_trash_list_and_restore(api, session_factory, db_run, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "docs"},
                      headers=auth(user)).json()["data"]
    f = _upload(api, user, b"hello", "a.txt", parent_id=folder["id"]).json()["data"]
    api.delete(f"/api/files/{folder['id']}", headers=auth(user))  # 目录整棵入回收站

    # 列表：含原位置与剩余天数
    resp = api.get("/api/trash", headers=auth(user))
    data = resp.json()["data"]
    assert data["total"] == 2
    item = next(i for i in data["items"] if i["id"] == folder["id"])
    assert item["is_folder"] is True
    assert item["remaining_days"] <= 30
    sub = next(i for i in data["items"] if i["id"] == f["id"])
    assert "docs" in sub["original_path"]

    # 还原目录 → 整棵子树复活
    resp = api.post(f"/api/trash/{folder['id']}/restore", headers=auth(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["restored_rows"] == 2

    async def check():
        async with session_factory() as db:
            for fid in (folder["id"], f["id"]):
                row = await db.get(UserFile, fid)
                assert row.status == "NORMAL" and row.alive == 1 and row.deleted_at is None
            versions = (await db.execute(select(FileVersion))).scalars().all()
            assert len(versions) == 1  # 引用未动

    db_run(check())


def test_restore_name_conflict_suggests(api, session_factory, db_run, make_user):
    user = make_user("alice")
    f1 = _upload(api, user, b"v1", "report.txt").json()["data"]
    api.delete(f"/api/files/{f1['id']}", headers=auth(user))
    _upload(api, user, b"newer", "report.txt")  # 原位新建同名

    resp = api.post(f"/api/trash/{f1['id']}/restore", headers=auth(user))
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == 2002
    assert body["data"]["suggested_name"] == "report（1）.txt"  # 序号插扩展名前（Windows 风格）

    # 用建议名成功还原：占位文件先行删除（进回收站），原名腾出后 v1 可原位复活
    newer_id = api.get("/api/folders/0/children", headers=auth(user)).json()["data"]["items"][0]["id"]
    api.delete(f"/api/files/{newer_id}", headers=auth(user))
    resp = api.post(f"/api/trash/{f1['id']}/restore", headers=auth(user))
    assert resp.status_code == 200

    async def check_final():
        async with session_factory() as db:
            restored = await db.get(UserFile, f1["id"])
            assert restored.status == "NORMAL"
            trashed = (await db.execute(select(UserFile).where(UserFile.status == "TRASH"))).scalars().all()
            assert len(trashed) == 1  # 只有占位 newer 留在回收站

    db_run(check_final())


def test_purge_releases_references_and_quota(api, session_factory, db_run, make_user):
    """T02 预演：甲乙同内容 → 同对象 ref=2；甲彻底删除 → 乙完整可下载，ref=1。"""
    alice = make_user("alice")
    bob = make_user("bob")
    content = b"shared-content-for-t02"
    a = _upload(api, alice, content, "shared.txt").json()["data"]
    b = _upload(api, bob, content, "shared.txt").json()["data"]
    assert a["storage_object_id"] == b["storage_object_id"]  # 物理去重

    # 甲进回收站 → 彻底删除
    api.delete(f"/api/files/{a['id']}", headers=auth(alice))
    resp = api.delete(f"/api/trash/{a['id']}", headers=auth(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["files"] == 1

    async def check_ref():
        async with session_factory() as db:
            obj = await db.get(StorageObject, a["storage_object_id"])
            assert obj.ref_count == 1  # 引用释放但对象仍被乙引用

    db_run(check_ref())

    # 乙完整可下载且哈希一致
    resp = api.get(f"/api/files/{b['id']}/download", headers=auth(bob))
    assert resp.status_code == 200
    assert resp.content == content

    # 甲的行变为 DELETED lineage
    async def check_lineage():
        async with session_factory() as db:
            row = await db.get(UserFile, a["id"])
            assert row.status == "DELETED"

    db_run(check_lineage())


def test_purge_unique_object_removes_minio_and_quota(api, session_factory, db_run, make_user):
    user = make_user("alice")
    content = b"only-mine"
    f = _upload(api, user, content, "mine.txt").json()["data"]

    async def usage_now():
        async with session_factory() as db:
            return (await db.execute(select(QuotaUsage))).scalar_one().used_bytes

    before = db_run(usage_now())
    assert before == len(content)

    api.delete(f"/api/files/{f['id']}", headers=auth(user))
    resp = api.delete(f"/api/trash/{f['id']}", headers=auth(user))
    assert resp.status_code == 200

    # 配额释放 + 对象行删除 + MinIO 字节删除
    assert db_run(usage_now()) == 0

    async def check_obj():
        async with session_factory() as db:
            rows = (await db.execute(select(StorageObject))).scalars().all()
            return [(r.status, r.ref_count) for r in rows]

    # M7.4 状态机：行保留转 DELETED（ref=0），字节已物理删除（内存桶已空）
    assert db_run(check_obj()) == [("DELETED", 0)]
    assert len(api.app.state.storage.objects) == 0


def test_purge_idempotent_404(api, make_user):
    user = make_user("alice")
    f = _upload(api, user, b"x", "once.txt").json()["data"]
    api.delete(f"/api/files/{f['id']}", headers=auth(user))
    assert api.delete(f"/api/trash/{f['id']}", headers=auth(user)).status_code == 200
    resp = api.delete(f"/api/trash/{f['id']}", headers=auth(user))
    assert resp.status_code == 404  # 二次彻底删除 → 幂等 404


def test_clear_trash_top_level_only(api, session_factory, db_run, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "dir"},
                      headers=auth(user)).json()["data"]
    f1 = _upload(api, user, b"1", "f1.txt", parent_id=folder["id"]).json()["data"]
    f2 = _upload(api, user, b"2", "f2.txt").json()["data"]
    api.delete(f"/api/files/{folder['id']}", headers=auth(user))  # 子树 2 项
    api.delete(f"/api/files/{f2['id']}", headers=auth(user))      # 独立 1 项

    resp = api.delete("/api/trash", headers=auth(user))  # 清空
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["roots"] == 2          # dir 与 f2 两个顶层
    assert body["files"] == 2          # f1+f2 两个文件释放

    async def check():
        async with session_factory() as db:
            rows = (await db.execute(select(UserFile).where(UserFile.status == "TRASH"))).scalars().all()
            assert rows == []                      # 回收站清空
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == 0           # 配额全部释放

    db_run(check())
