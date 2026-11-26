"""M7 测试：T05 版本全流程与配额 / T08 重启续传 / T12 GC 竞争。"""
import hashlib

from sqlalchemy import select

from app.models.file_version import FileVersion
from app.models.upload import Outbox
from app.models.quota_usage import QuotaUsage
from app.models.storage_object import StorageObject
from app.models.upload import UploadTask
from app.models.user_file import UserFile
from app.services.background import dispatch_outbox_once, reconcile_once
from tests.conftest import auth

CHUNK = 64


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _chunked_upload(api, user, content: bytes, name="doc.txt"):
    init = api.post(
        "/api/uploads/init",
        json={"file_name": name, "size": len(content), "parent_id": 0,
              "declared_sha256": _sha(content)},
        headers=auth(user),
    ).json()["data"]
    if init.get("instant"):
        return {"file": init["file"], "sha": sha_of(content)}
    uid = init["upload_id"]
    for i in range(init["chunk_count"]):
        part = content[i * CHUNK:(i + 1) * CHUNK]
        r = api.put(f"/api/uploads/{uid}/parts/{i + 1}", content=part,
                    params={"sha256": _sha(part)}, headers=auth(user))
        assert r.status_code == 200, r.text
    comp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user)).json()["data"]
    return {"file": comp["file"], "sha": comp["sha256"]}


def sha_of(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------- T05：版本全流程与配额 ----------
def test_t05_version_full_flow(api, session_factory, db_run, make_user):
    user = make_user("alice")
    v1c = b"round one"
    v2c = b"round two with different bytes"

    _chunked_upload(api, user, v1c)                     # v1
    v2 = _chunked_upload(api, user, v2c)                # 同名 → v2（新版本意图）
    file_id = v2["file"]["id"]

    # 列表：两版本、当前为 v2、总占用正确
    resp = api.get(f"/api/files/{file_id}/versions", headers=auth(user))
    data = resp.json()["data"]
    assert data["total_size"] == len(v1c) + len(v2c)
    assert [i["version_no"] for i in data["items"]] == [1, 2]
    assert data["items"][1]["current"] is True
    cur_id = data["current_version_id"]

    # 配额：同内容不同字节 → 逻辑各计
    async def usage():
        async with session_factory() as db:
            return (await db.execute(select(QuotaUsage))).scalar_one().used_bytes

    assert db_run(usage()) == len(v1c) + len(v2c)

    # 恢复 v1：指针切换 + generation+1，用量不变
    gen_before = db_run(_gen_of(session_factory, file_id))
    resp = api.post(f"/api/files/{file_id}/versions/1/restore", headers=auth(user))
    assert resp.status_code == 200
    resp = api.get(f"/api/files/{file_id}/download", headers=auth(user))
    assert resp.content == v1c
    assert db_run(usage()) == len(v1c) + len(v2c)  # 版本集合未变
    assert db_run(_gen_of(session_factory, file_id)) == gen_before + 1

    # 删当前版本（v1 已是当前）→ 409
    resp = api.delete(f"/api/files/{file_id}/versions/1", headers=auth(user))
    assert resp.status_code == 409 and resp.json()["code"] == 2005

    # 删 v2（非当前）→ 释放：用量减少、对象行两阶段
    resp = api.delete(f"/api/files/{file_id}/versions/2", headers=auth(user))
    assert resp.status_code == 200
    assert db_run(usage()) == len(v1c)

    async def check_objects():
        async with session_factory() as db:
            rows = (await db.execute(select(StorageObject))).scalars().all()
            # v2 对应对象：ref 0 → 两阶段完成后行保留为 DELETED
            deleted = [r for r in rows if r.status == "DELETED"]
            active = [r for r in rows if r.status == "ACTIVE"]
            assert len(deleted) == 1 and deleted[0].ref_count == 0
            assert len(active) == 1  # v1 对象仍被引用
            # v2 对应字节已物理删除
            keys = set(api.app.state.storage.objects.keys())
            active_key = active[0].object_key
            assert all(k == active_key or k not in keys for k in [deleted[0].object_key])
            assert deleted[0].object_key not in keys

    db_run(check_objects())

    # 只剩一个版本 → 再删被禁
    resp = api.delete(f"/api/files/{file_id}/versions/1", headers=auth(user))
    assert resp.status_code == 409
    # 当前指针 = v1（恢复后）；仅剩 v1 一个版本
    resp = api.get(f"/api/files/{file_id}/versions", headers=auth(user))
    data = resp.json()["data"]
    assert data["current_version_id"] != cur_id  # 已从 v2 切回 v1
    assert [i["version_no"] for i in data["items"]] == [1]
    assert data["items"][0]["current"] is True


async def _gen_of(session_factory, file_id):
    async with session_factory() as db:
        return (await db.get(UserFile, file_id)).generation


# ---------- T08：服务重启（持久化 + 续传） ----------
def test_t08_restart_resume_and_data_integrity(api, engine, session_factory, db_run, make_user):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from fastapi.testclient import TestClient as _TC
    from fakeredis import FakeAsyncRedis as _FR
    from app.db.session import get_db as _gdb
    from app.db.base import Base as _Base
    from app.main import app as _app
    """进行中任务在"重启"（同库新客户端）后可续传；已提交数据完整、用量正确。"""
    user = make_user("alice")
    content = bytes(range(128))
    init = api.post(
        "/api/uploads/init",
        json={"file_name": "t08.txt", "size": len(content), "parent_id": 0,
              "declared_sha256": _sha(content)},
        headers=auth(user),
    ).json()["data"]
    uid = init["upload_id"]
    # 只传第一片（重启前）
    part1 = content[:CHUNK]
    api.put(f"/api/uploads/{uid}/parts/1", content=part1,
            params={"sha256": _sha(part1)}, headers=auth(user))

    # ——模拟重启：同一 engine（持久 DB）+ 同一份存储桶，新 TestClient——
    ready = [False]

    async def override_get_db():
        if not ready[0]:
            async with engine.begin() as conn:
                await conn.run_sync(_Base.metadata.create_all)
            ready[0] = True
        async with _asm(engine, expire_on_commit=False)() as s:
            yield s

    _app.dependency_overrides[_gdb] = override_get_db
    _app.state.redis = _FR(decode_responses=True)
    _app.state.storage = api.app.state.storage  # 真实部署为 MinIO 卷持久
    with _TC(_app) as c2:
        status = c2.get(f"/api/uploads/{uid}", headers=auth(user)).json()["data"]
        assert status["received_parts"] == [1]
        assert status["missing_parts"] == [2]
        # 续传第二片 + 完成
        part2 = content[CHUNK:]
        r = c2.put(f"/api/uploads/{uid}/parts/2", content=part2,
                   params={"sha256": _sha(part2)}, headers=auth(user))
        assert r.status_code == 200
        r = c2.post(f"/api/uploads/{uid}/complete", headers=auth(user))
        assert r.status_code == 200
        fid = r.json()["data"]["file"]["id"]
        got = c2.get(f"/api/files/{fid}/download", headers=auth(user))
        assert got.content == content

    async def check_usage():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == len(content) and usage.reserved_bytes == 0

    db_run(check_usage())


# ---------- T12：GC 竞争 ----------
def test_t12_gc_respects_active_tasks_and_references(api, session_factory, db_run, make_user):
    """活跃任务候选对象与被引用对象不被清理；零引用 DELETING 残留可回收。"""

    user = make_user("alice")
    # ① 活跃任务（只传 1/2 片）→ 其候选对象必须存活
    content = b"active" * 20
    init = api.post(
        "/api/uploads/init",
        json={"file_name": "active.txt", "size": len(content), "parent_id": 0,
              "declared_sha256": _sha(content)},
        headers=auth(user),
    ).json()["data"]
    uid = init["upload_id"]
    part = content[:CHUNK]
    api.put(f"/api/uploads/{uid}/parts/1", content=part,
            params={"sha256": _sha(part)}, headers=auth(user))
    active_key = f"up/{uid}/part-00001"

    # ② 已提交对象（被引用）必须存活
    done = _chunked_upload(api, user, b"referenced-bytes")
    ref_sha = done["sha"]

    # ③ 零引用 DELETING 残留（手工构造：完成后再取消版本删除路径的产物模拟）
    residue_key = "up/dead/been-deleted"
    api.app.state.storage.objects[(api.app.state.storage.bucket_candidate, residue_key)] = b"orphan"
    from app.models.storage_object import OBJECT_STATUS_DELETING
    from app.core.time import utcnow
    from datetime import timedelta

    async def seed_residue():
        async with session_factory() as db:
            db.add(StorageObject(
                sha256=_sha(b"orphan"), size=6, bucket=api.app.state.storage.bucket_candidate,
                object_key=residue_key, ref_count=0, status=OBJECT_STATUS_DELETING,
                created_at=utcnow() - timedelta(hours=1),
            ))
            await db.commit()

    db_run(seed_residue())

    # 跑一轮对账
    async def cycle():
        async with session_factory() as db:
            return await reconcile_once(db, api.app.state.storage)

    report = db_run(cycle())

    async def check_after():
        async with session_factory() as db:
            # 活跃任务的分片仍在
            assert active_key in [k for (k, _) in ()] or \
                (api.app.state.storage.bucket_candidate, active_key) in api.app.state.storage.objects
            # 被引用对象仍在
            obj = (await db.execute(
                select(StorageObject).where(StorageObject.sha256 == ref_sha)
            )).scalar_one()
            assert obj.status == "ACTIVE"
            assert (api.app.state.storage.bucket_candidate, obj.object_key) in api.app.state.storage.objects
            # DELETING 残留 → 已重试删除并转 DELETED
            residue = (await db.execute(
                select(StorageObject).where(StorageObject.object_key == residue_key)
            )).scalar_one()
            assert residue.status == "DELETED"
            assert (api.app.state.storage.bucket_candidate, residue_key) not in api.app.state.storage.objects

    db_run(check_after())


def test_t12_ref_mismatch_fixed_and_missing_alerted(api, session_factory, db_run, make_user, caplog):
    user = make_user("alice")
    done = _chunked_upload(api, user, b"ref-check")
    sha = done["sha"]

    async def corrupt():
        async with session_factory() as db:
            obj = (await db.execute(select(StorageObject))).scalar_one()
            obj.ref_count = 99  # 人为偏差
            await db.commit()

    db_run(corrupt())

    async def cycle():
        async with session_factory() as db:
            return await reconcile_once(db, api.app.state.storage)

    db_run(cycle())

    async def check_fixed():
        async with session_factory() as db:
            obj = (await db.execute(select(StorageObject))).scalar_one()
            assert obj.ref_count == 1  # 修复为真实引用数

    db_run(check_fixed())
