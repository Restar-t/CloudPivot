"""M2 空间与目录树：注册事务化 / 文件夹 CRUD / 分页排序 / 面包屑 / 重命名。"""
import asyncio

from sqlalchemy import select

from app.models.space import ROLE_OWNER, SPACE_TYPE_PERSONAL, Space, SpaceMember
from app.models.user_file import UserFile
from tests.conftest import auth


def test_register_creates_personal_space(api, session_factory, make_user):
    make_user("alice")

    async def check():
        async with session_factory() as db:
            space = (await db.execute(select(Space))).scalar_one()
            assert space.type == SPACE_TYPE_PERSONAL
            assert space.owner_id is not None
            member = (await db.execute(select(SpaceMember))).scalar_one()
            assert member.role == ROLE_OWNER

    asyncio.run(check())


def test_create_folder_and_duplicate(api, make_user):
    user = make_user("alice")
    resp = api.post("/api/folders", json={"parent_id": 0, "name": "文档"},
                    headers=auth(user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "文档"
    assert data["is_folder"] is True
    folder_id = data["id"]

    # 同目录活跃重名 → 409 / 2002
    resp = api.post("/api/folders", json={"parent_id": 0, "name": "文档"}, headers=auth(user))
    assert resp.status_code == 409
    assert resp.json()["code"] == 2002

    # 子目录
    resp = api.post("/api/folders", json={"parent_id": folder_id, "name": "2026"}, headers=auth(user))
    assert resp.status_code == 200


def test_create_folder_invalid_names(api, make_user):
    user = make_user("alice")
    for name in ["..", "a/b", "CON", "bad*name", "ends."]:
        resp = api.post("/api/folders", json={"parent_id": 0, "name": name}, headers=auth(user))
        assert resp.status_code == 400, name
        assert resp.json()["code"] == 2003, name


def test_create_folder_parent_not_found(api, make_user):
    user = make_user("alice")
    resp = api.post("/api/folders", json={"parent_id": 99999, "name": "x"}, headers=auth(user))
    assert resp.status_code == 404
    assert resp.json()["code"] == 2001


def test_children_listing_pagination_and_sort(api, make_user):
    user = make_user("alice")
    ids = {}
    for name in ["b_dir", "a_dir", "c_dir"]:
        resp = api.post("/api/folders", json={"parent_id": 0, "name": name}, headers=auth(user))
        ids[name] = resp.json()["data"]["id"]
    api.post("/api/folders", json={"parent_id": ids["a_dir"], "name": "sub"}, headers=auth(user))

    # 全量 + 名称排序
    resp = api.get("/api/folders/0/children", headers=auth(user))
    data = resp.json()["data"]
    assert data["total"] == 3
    assert [i["name"] for i in data["items"]] == ["a_dir", "b_dir", "c_dir"]

    # 分页
    resp = api.get("/api/folders/0/children", params={"page": 2, "page_size": 2}, headers=auth(user))
    data = resp.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 1

    # 降序
    resp = api.get("/api/folders/0/children", params={"order": "desc"}, headers=auth(user))
    data = resp.json()["data"]
    assert data["items"][0]["name"] == "c_dir"

    # 非法排序字段 → 422 / 9001
    resp = api.get("/api/folders/0/children", params={"sort": "password"}, headers=auth(user))
    assert resp.status_code == 422
    assert resp.json()["code"] == 9001


def test_path_breadcrumb(api, make_user):
    user = make_user("alice")
    a = api.post("/api/folders", json={"parent_id": 0, "name": "a"}, headers=auth(user)).json()["data"]["id"]
    b = api.post("/api/folders", json={"parent_id": a, "name": "b"}, headers=auth(user)).json()["data"]["id"]
    c = api.post("/api/folders", json={"parent_id": b, "name": "c"}, headers=auth(user)).json()["data"]["id"]

    resp = api.get(f"/api/folders/{c}/path", headers=auth(user))
    chain = resp.json()["data"]["items"]
    assert [(x["id"], x["name"]) for x in chain] == [(0, "根目录"), (a, "a"), (b, "b"), (c, "c")]

    # 根
    resp = api.get("/api/folders/0/path", headers=auth(user))
    assert [(x["id"], x["name"]) for x in resp.json()["data"]["items"]] == [(0, "根目录")]


def test_rename_folder(api, session_factory, make_user):
    user = make_user("alice")
    folder = api.post("/api/folders", json={"parent_id": 0, "name": "old"}, headers=auth(user)).json()["data"]
    other = api.post("/api/folders", json={"parent_id": 0, "name": "other"}, headers=auth(user)).json()["data"]

    resp = api.patch(f"/api/files/{folder['id']}/rename", json={"new_name": "new"},
                     headers=auth(user))
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "new"

    async def check_generation():
        async with session_factory() as db:
            row = await db.get(UserFile, folder["id"])
            assert row.generation == 1  # 改名必须 bump（M6 上传冲突校验依赖）

    asyncio.run(check_generation())

    # 改成现有同名 → 409
    resp = api.patch(f"/api/files/{folder['id']}/rename", json={"new_name": "other"},
                     headers=auth(user))
    assert resp.status_code == 409
    assert resp.json()["code"] == 2002

    # 非法名 → 400
    resp = api.patch(f"/api/files/{folder['id']}/rename", json={"new_name": ".."}, headers=auth(user))
    assert resp.status_code == 400

    # 不存在 → 404
    resp = api.patch("/api/files/99999/rename", json={"new_name": "x"}, headers=auth(user))
    assert resp.status_code == 404


def test_is_ancestor_tool(api, session_factory, make_user):
    """循环祖先校验（M4 移动的循环检测基础）。"""
    from app.services.folder_service import is_ancestor

    user = make_user("alice")
    a = api.post("/api/folders", json={"parent_id": 0, "name": "a"}, headers=auth(user)).json()["data"]["id"]
    b = api.post("/api/folders", json={"parent_id": a, "name": "b"}, headers=auth(user)).json()["data"]["id"]

    async def main():
        async with session_factory() as db:
            assert await is_ancestor(db, a, b) is True  # a 是 b 的祖先
            assert await is_ancestor(db, b, a) is False
            assert await is_ancestor(db, b, b) is True  # 自身也算（移动到自身必须拒绝）
            assert await is_ancestor(db, a, 0) is False

    asyncio.run(main())
