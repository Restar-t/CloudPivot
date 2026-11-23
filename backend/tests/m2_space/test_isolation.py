"""M2.9 隔离与 RBAC 用例（T07 的地基）。

- 非成员访问他人资源 → 统一 404 / 2001（不泄露存在性）
- 成员但角色不足 → 403 / 6001（VIEWER 只读：可列表，不可建/改名）
- 无 token → 401 / 1001
"""
import asyncio

from sqlalchemy import select

from app.models.space import ROLE_VIEWER, SpaceMember
from app.models.user_file import UserFile
from tests.conftest import auth


def test_no_token_rejected(api):
    assert api.get("/api/folders/0/children").status_code == 401
    assert api.post("/api/folders", json={"parent_id": 0, "name": "x"}).status_code == 401
    assert api.get("/api/folders/0/path").status_code == 401


def test_cross_user_isolation_404(api, make_user):
    alice = make_user("alice")
    bob = make_user("bob")

    folder = api.post(
        "/api/folders", json={"parent_id": 0, "name": "alice-secret"}, headers=auth(alice)
    ).json()["data"]

    # B 用 A 的 folder_id：列表 / 面包屑 / 改名 / 建子目录 全部 404
    assert api.get(f"/api/folders/{folder['id']}/children", headers=auth(bob)).status_code == 404
    assert api.get(f"/api/folders/{folder['id']}/path", headers=auth(bob)).status_code == 404
    resp = api.patch(f"/api/files/{folder['id']}/rename", json={"new_name": "hacked"},
                     headers=auth(bob))
    assert resp.status_code == 404
    assert resp.json()["code"] == 2001
    resp = api.post("/api/folders", json={"parent_id": folder["id"], "name": "inject"},
                    headers=auth(bob))
    assert resp.status_code == 404

    # B 自己的根是空的：A 的目录不出现在 B 的列表
    resp = api.get("/api/folders/0/children", headers=auth(bob))
    assert resp.json()["data"]["total"] == 0


def test_viewer_role_readonly(api, session_factory, make_user):
    """注入 VIEWER 成员（M8 前无成员管理接口，直接写库构造场景）。"""
    alice = make_user("alice")
    bob = make_user("bob")

    folder = api.post(
        "/api/folders", json={"parent_id": 0, "name": "shared"}, headers=auth(alice)
    ).json()["data"]

    async def add_viewer():
        async with session_factory() as db:
            space_id = (await db.execute(
                select(UserFile.space_id).where(UserFile.id == folder["id"])
            )).scalar_one()
            db.add(SpaceMember(space_id=space_id, user_id=bob["user"]["id"], role=ROLE_VIEWER))
            await db.commit()

    asyncio.run(add_viewer())

    # VIEWER：可列表、可看面包屑
    assert api.get(f"/api/folders/{folder['id']}/children", headers=auth(bob)).status_code == 200
    assert api.get(f"/api/folders/{folder['id']}/path", headers=auth(bob)).status_code == 200

    # VIEWER：不可建文件夹、不可改名 → 403 / 6001
    resp = api.post("/api/folders", json={"parent_id": folder["id"], "name": "v"},
                    headers=auth(bob))
    assert resp.status_code == 403
    assert resp.json()["code"] == 6001
    resp = api.patch(f"/api/files/{folder['id']}/rename", json={"new_name": "v2"},
                     headers=auth(bob))
    assert resp.status_code == 403
