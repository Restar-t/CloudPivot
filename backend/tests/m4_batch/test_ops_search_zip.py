"""M4 操作与搜索测试：移动（循环/冲突）/ 复制（ref++/配额）/ 批量删除 / ZIP 哈希 / 搜索转义。"""
import hashlib
import io
import zipfile

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


# ---------- 移动 ----------
def test_move_file_and_generation_bump(api, session_factory, db_run, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "dest"},
                      headers=auth(user)).json()["data"]
    f = _upload(api, user, b"move-me", "m.txt").json()["data"]

    resp = api.post("/api/files/move", json={"file_ids": [f["id"]], "target_folder_id": folder["id"]},
                    headers=auth(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["results"][0]["ok"] is True

    async def check():
        async with session_factory() as db:
            row = await db.get(UserFile, f["id"])
            assert row.parent_id == folder["id"]
            assert row.generation == 1  # 移动必须 bump（M6 上传冲突校验依赖）

    db_run(check())

    # 幂等：再移一次到同目录 → ok 且不变
    resp = api.post("/api/files/move", json={"file_ids": [f["id"]], "target_folder_id": folder["id"]},
                    headers=auth(user))
    assert resp.json()["data"]["results"][0]["ok"] is True


def test_move_cycle_and_conflict(api, session_factory, db_run, make_user):
    user = make_user("alice")
    outer = api.post("/api/folders", json={"parent_id": 0, "name": "outer"},
                     headers=auth(user)).json()["data"]
    inner = api.post("/api/folders", json={"parent_id": outer["id"], "name": "inner"},
                     headers=auth(user)).json()["data"]

    # 目录移入自己的子孙 → 2004
    resp = api.post("/api/files/move", json={"file_ids": [outer["id"]], "target_folder_id": inner["id"]},
                    headers=auth(user))
    assert resp.status_code == 200  # 批量接口单项失败也是 200
    r = resp.json()["data"]["results"][0]
    assert r["ok"] is False and r["code"] == 2004

    # 目标目录重名 → 2002
    _upload(api, user, b"dup", "dup.txt")
    f2 = _upload(api, user, b"dup", "dup.txt")  # 同根同名会被拒 → 换个子目录场景
    assert f2.status_code == 409
    sub = api.post("/api/folders", json={"parent_id": 0, "name": "sub"}, headers=auth(user)).json()["data"]
    f3 = _upload(api, user, b"x", "dup.txt", parent_id=sub["id"]).json()["data"]
    dup_root = _upload(api, user, b"dup", "dup.txt")  # 根目录占位（移走子目录里的）
    assert dup_root.status_code == 409
    _upload(api, user, b"root-dup", "dup2.txt")
    # 把 sub/dup.txt 移到根 → 根已有同名 → 409
    resp = api.post("/api/files/move", json={"file_ids": [f3["id"]], "target_folder_id": 0},
                    headers=auth(user))
    r = resp.json()["data"]["results"][0]
    assert r["ok"] is False and r["code"] == 2002


def test_move_nonexistent_and_404(api, make_user):
    user = make_user("alice")
    resp = api.post("/api/files/move", json={"file_ids": [99999], "target_folder_id": 0},
                    headers=auth(user))
    r = resp.json()["data"]["results"][0]
    assert r["ok"] is False and r["code"] == 2001


# ---------- 复制 ----------
def test_copy_file_ref_count_and_quota(api, session_factory, db_run, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "dst"},
                      headers=auth(user)).json()["data"]
    content = b"copy-me"
    f = _upload(api, user, content, "c.txt").json()["data"]

    resp = api.post("/api/files/copy", json={"file_ids": [f["id"]], "target_folder_id": folder["id"]},
                    headers=auth(user))
    assert resp.status_code == 200
    r = resp.json()["data"]["results"][0]
    assert r["ok"] is True
    new_id = r["new_ids"][0]

    async def check():
        async with session_factory() as db:
            clone = await db.get(UserFile, new_id)
            assert clone.parent_id == folder["id"] and clone.name == "c.txt"
            obj = await db.get(StorageObject, f["storage_object_id"])
            assert obj.ref_count == 2      # 复制共享对象
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == 2 * len(content)  # 逻辑配额双计

    db_run(check())

    # 两份都可下载
    for fid in (f["id"], new_id):
        resp = api.get(f"/api/files/{fid}/download", headers=auth(user))
        assert resp.content == content


def test_copy_folder_recursive(api, session_factory, db_run, make_user):
    user = make_user("alice")
    src = api.post("/api/folders", json={"parent_id": 0, "name": "src"},
                   headers=auth(user)).json()["data"]
    sub = api.post("/api/folders", json={"parent_id": src["id"], "name": "sub"},
                   headers=auth(user)).json()["data"]
    f1 = _upload(api, user, b"one", "one.txt", parent_id=src["id"]).json()["data"]
    f2 = _upload(api, user, b"two", "two.txt", parent_id=sub["id"]).json()["data"]

    resp = api.post("/api/files/copy", json={"file_ids": [src["id"]], "target_folder_id": 0},
                    headers=auth(user))
    r = resp.json()["data"]["results"][0]
    assert r["ok"] is True and len(r["new_ids"]) == 4  # 目录+子目录+2文件

    async def check():
        async with session_factory() as db:
            obj = await db.get(StorageObject, f1["storage_object_id"])
            assert obj.ref_count == 2  # f1 被复制
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == 12  # one(3B)+two(3B) 各 2 份 = 12

    db_run(check())


# ---------- 批量删除 ----------
def test_batch_delete_partial_failure(api, session_factory, db_run, make_user):
    user = make_user("alice")
    f1 = _upload(api, user, b"1", "b1.txt").json()["data"]
    f2 = _upload(api, user, b"2", "b2.txt").json()["data"]
    bob = make_user("bob")
    foreign = _upload(api, bob, b"x", "x.txt").json()["data"]

    resp = api.post("/api/files/batch-delete",
                    json={"file_ids": [f1["id"], foreign["id"], f2["id"], 99999]},
                    headers=auth(user))
    results = resp.json()["data"]["results"]
    assert [r["ok"] for r in results] == [True, False, True, False]
    assert results[1]["code"] == 2001 and results[3]["code"] == 2001  # 越权/不存在均 404

    async def check():
        async with session_factory() as db:
            for fid in (f1["id"], f2["id"]):
                assert (await db.get(UserFile, fid)).status == "TRASH"
            assert (await db.get(UserFile, foreign["id"])).status == "NORMAL"  # 未受影响

    db_run(check())


# ---------- ZIP ----------
def test_zip_download_hashes(api, make_user):
    user = make_user("alice")
    c1, c2 = b"first-file-content", b"second-" * 100
    f1 = _upload(api, user, c1, "first.txt").json()["data"]
    f2 = _upload(api, user, c2, "second.txt").json()["data"]

    resp = api.get(f"/api/files/zip?ids={f1['id']},{f2['id']}", headers=auth(user))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert "attachment" in resp.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == ["first.txt", "second.txt"]
    assert zf.read("first.txt") == c1
    assert zf.read("second.txt") == c2
    assert zf.testzip() is None  # CRC 全通过


def test_zip_cross_user_rejected_whole_request(api, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    mine = _upload(api, alice, b"mine", "m.txt").json()["data"]
    theirs = _upload(api, bob, b"theirs", "t.txt").json()["data"]

    # 混入无权文件 → 整体 404（不发半截包）
    resp = api.get(f"/api/files/zip?ids={mine['id']},{theirs['id']}", headers=auth(alice))
    assert resp.status_code == 404
    # 只有有权文件 → 正常
    resp = api.get(f"/api/files/zip?ids={mine['id']}", headers=auth(alice))
    assert resp.status_code == 200


# ---------- 搜索 ----------
def test_search_match_pagination_sort(api, make_user):
    user = make_user("alice")
    _upload(api, user, b"1", "季度报告2026.txt")
    _upload(api, user, b"2", "季度报告2025.txt")
    _upload(api, user, b"3", "季度报告2024.txt")
    _upload(api, user, b"4", "无关文件.txt")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "季度报告目录"},
                      headers=auth(user)).json()["data"]

    resp = api.get("/api/files/search", params={"q": "季度报告"}, headers=auth(user))
    data = resp.json()["data"]
    assert data["total"] == 4  # 3 文件 + 1 目录（目录也参与名称匹配）
    assert data["items"][0]["is_folder"] is True  # 文件夹置顶

    # 分页
    resp = api.get("/api/files/search", params={"q": "季度报告", "page": 2, "page_size": 2},
                   headers=auth(user))
    assert resp.json()["data"]["total"] == 4
    assert len(resp.json()["data"]["items"]) == 2

    # 排序：按名称降序
    resp = api.get("/api/files/search",
                   params={"q": "季度报告", "sort": "name", "order": "desc"}, headers=auth(user))
    names = [i["name"] for i in resp.json()["data"]["items"] if not i["is_folder"]]
    assert names == ["季度报告2026.txt", "季度报告2025.txt", "季度报告2024.txt"]


def test_search_wildcards_escaped_and_isolation(api, make_user):
    user = make_user("alice")
    bob = make_user("bob")
    _upload(api, user, b"1", "100%折扣.txt")
    _upload(api, user, b"2", "abc_def.txt")

    # % 与 _ 是 LIKE 通配符，作为字面量匹配
    resp = api.get("/api/files/search", params={"q": "100%折扣"}, headers=auth(user))
    assert resp.json()["data"]["total"] == 1
    resp = api.get("/api/files/search", params={"q": "abc_def"}, headers=auth(user))
    assert resp.json()["data"]["total"] == 1
    # 不转义的话 "%x%" 会匹配一切——验证字面 % 不当通配符
    resp = api.get("/api/files/search", params={"q": "%x%"}, headers=auth(user))
    assert resp.json()["data"]["total"] == 0

    # 搜索只命中自己空间：bob 搜不到 alice 的文件
    resp = api.get("/api/files/search", params={"q": "折扣"}, headers=auth(bob))
    assert resp.json()["data"]["total"] == 0
    resp = api.get("/api/files/search", params={"q": "折扣"}, headers=auth(user))
    assert resp.json()["data"]["total"] == 1
