"""M6 分片上传测试：预留/分片校验/断点续传/取消释放/T03 序/并发完成/秒传 T13/generation 冲突。"""
import asyncio
import hashlib
import uuid

from sqlalchemy import select

from app.models.upload import Outbox
from app.models.quota_usage import QuotaUsage
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.upload import TASK_FAILED, UploadPart, UploadTask
from app.models.user_file import UserFile
from tests.conftest import auth

CHUNK = 64


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _init(api, user, name, size, parent_id=0, sha=None):
    return api.post(
        "/api/uploads/init",
        json={"file_name": name, "size": size, "parent_id": parent_id,
              **({"declared_sha256": sha} if sha else {})},
        headers=auth(user),
    )


def _part(api, user, uid, part_no, content: bytes):
    return api.put(
        f"/api/uploads/{uid}/parts/{part_no}",
        content=content,
        params={"sha256": _sha(content)},
        headers=auth(user),
    )


def _complete_chunks(api, user, content: bytes, chunk: int = CHUNK):
    """按分片顺序上传全部内容后 complete。"""
    init = _init(api, user, "f.txt", len(content), sha=_sha(content)).json()["data"]
    if init.get("instant"):
        return {"instant": True, **init}
    uid = init["upload_id"]
    for i in range(init["chunk_count"]):
        part = content[i * chunk:(i + 1) * chunk]
        resp = _part(api, user, uid, i + 1, part)
        assert resp.status_code == 200, resp.text
    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return {"instant": False, "upload_id": uid, **resp.json()["data"]}


def test_init_reserves_quota_atomically(api, session_factory, db_run, make_user):
    user = make_user("alice")
    size = 100
    resp = _init(api, user, "big.txt", size, sha=_sha(b"a" * size))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["instant"] is False
    assert data["chunk_count"] == 2  # 100B / 64B → 2 分片
    uid = data["upload_id"]

    async def check():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes == size and usage.used_bytes == 0
            task = (await db.execute(select(UploadTask))).scalar_one()
            assert task.status == "CREATED" and task.reserved_bytes == size

    db_run(check())
    # 取消释放（一次）
    api.post(f"/api/uploads/{uid}/cancel", headers=auth(user))
    async def check2():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes == 0
    db_run(check2())


def test_init_quota_exceeded_no_task(api, session_factory, db_run, make_user):
    """额度调小后 init 超限 → 507，且不创建任务、不留预留。"""
    from sqlalchemy import update as _update
    user = make_user("alice")

    async def shrink():
        async with session_factory() as db:
            await db.execute(_update(User).where(User.username == "alice").values(quota_bytes=10))
            await db.commit()

    db_run(shrink())
    resp = _init(api, user, "huge.txt", 100)  # 100B：通过 1KiB 文件上限，超 10B 额度
    assert resp.status_code == 507
    body = resp.json()
    assert body["code"] == 5001

    async def check():
        async with session_factory() as db:
            tasks = (await db.execute(select(UploadTask))).scalars().all()
            usage = (await db.execute(select(QuotaUsage))).scalar_one_or_none()
            assert tasks == []                      # 未创建任务
            assert usage is None or usage.reserved_bytes == 0  # 无预留

    db_run(check())


def test_part_validation_and_idempotency(api, session_factory, db_run, make_user):
    user = make_user("alice")
    content = b"x" * 100
    init = _init(api, user, "f.txt", 100).json()["data"]
    uid = init["upload_id"]

    # 错误校验和 → 3003
    resp = api.put(f"/api/uploads/{uid}/parts/1", content=b"x" * 64,
                   params={"sha256": _sha(b"wrong")}, headers=auth(user))
    assert resp.status_code == 422 and resp.json()["code"] == 3003

    # 大小不符 → 409 / 3004
    resp = _part(api, user, uid, 1, b"short")
    assert resp.status_code == 409 and resp.json()["code"] == 3004

    # 正确
    resp = _part(api, user, uid, 1, content[:64])
    assert resp.status_code == 200
    assert resp.json()["data"]["received"] == 1

    # 同号同内容 → 幂等（received 不变）
    resp = _part(api, user, uid, 1, content[:64])
    assert resp.status_code == 200
    assert resp.json()["data"]["received"] == 1

    # 同号不同内容 → 409
    resp = _part(api, user, uid, 1, b"y" * 64)
    assert resp.status_code == 409

    # 越界分片号 → 400
    resp = _part(api, user, uid, 99, b"z" * 36)
    assert resp.status_code == 422


def test_status_missing_parts_and_resume(api, session_factory, db_run, make_user):
    user = make_user("alice")
    content = bytes(range(100))
    init = _init(api, user, "resume.txt", 100).json()["data"]
    uid = init["upload_id"]
    _part(api, user, uid, 1, content[:64])  # 只传第一片

    resp = api.get(f"/api/uploads/{uid}", headers=auth(user))
    data = resp.json()["data"]
    assert data["received_parts"] == [1]
    assert data["missing_parts"] == [2]

    # 断点续传：补第二片 → complete
    _part(api, user, uid, 2, content[64:])
    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["sha256"] == _sha(content)

    # 下载核验
    resp = api.get(f"/api/files/{body['file']['id']}/download", headers=auth(user))
    assert resp.content == content


def test_complete_sha_mismatch_marks_failed_and_releases(api, session_factory, db_run, make_user):
    """声明 sha 与实际合并内容不符 → FAILED 终态 + 预留释放。"""
    user = make_user("alice")
    content = b"y" * 100
    init = _init(api, user, "bad.txt", 100, sha=_sha(b"z" * 100)).json()["data"]  # 声明错
    uid = init["upload_id"]
    _part(api, user, uid, 1, content[:64])
    _part(api, user, uid, 2, content[64:])

    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    assert resp.status_code == 422
    assert resp.json()["code"] == 3003

    async def check():
        async with session_factory() as db:
            task = (await db.execute(select(UploadTask))).scalar_one()
            assert task.status == "FAILED"
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes == 0  # 预留释放

    db_run(check())

    # 终态不可复活：补传分片/再完成 → 409
    assert _part(api, user, uid, 1, content[:64]).status_code == 409
    assert api.post(f"/api/uploads/{uid}/complete", headers=auth(user)).status_code == 409


def test_t03_cancel_then_complete_rejected(api, session_factory, db_run, make_user):
    """T03 序一：取消先提交 → complete 被拒；预留只释放一次。"""
    user = make_user("alice")
    content = b"c" * 100
    init = _init(api, user, "race.txt", 100).json()["data"]
    uid = init["upload_id"]
    _part(api, user, uid, 1, content[:64])
    _part(api, user, uid, 2, content[64:])

    r1 = api.post(f"/api/uploads/{uid}/cancel", headers=auth(user)).json()["data"]
    assert r1["cancelled"] is True and r1["duplicate"] is False
    r2 = api.post(f"/api/uploads/{uid}/cancel", headers=auth(user)).json()["data"]
    assert r2["cancelled"] is True and r2["duplicate"] is True  # 幂等

    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    assert resp.status_code == 409

    async def check_once():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes == 0  # 未双重释放（无负值）

    db_run(check_once())


def test_t03_complete_then_cancel_returns_completed(api, make_user):
    """T03 序二：完成先提交 → 取消请求返回"已完成"。"""
    user = make_user("alice")
    result = _complete_chunks(api, user, b"done-content")  # 内部走完整流程
    assert result["instant"] is False
    uid = result["upload_id"]
    resp = api.post(f"/api/uploads/{uid}/cancel", headers=auth(user))
    data = resp.json()["data"]
    assert data["completed"] is True and data.get("cancelled") is False


def test_same_name_binds_new_version(api, session_factory, db_run, make_user):
    """同名上传 → init 自动绑定 target_file_id + generation 快照 → 完成生成 v2。"""
    user = make_user("alice")
    v1 = _complete_chunks(api, user, b"first round content")
    file_id = v1["file"]["id"]

    content2 = b"second round with more bytes"
    init = _init(api, user, "f.txt", len(content2)).json()["data"]
    assert init["target_file_id"] == file_id  # 新版本意图
    uid = init["upload_id"]
    for i in range(init["chunk_count"]):
        _part(api, user, uid, i + 1, content2[i * CHUNK:(i + 1) * CHUNK])
    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    body = resp.json()["data"]
    assert body["version_no"] == 2

    async def check():
        async with session_factory() as db:
            row = await db.get(UserFile, file_id)
            assert row.generation == 1  # 新版本 bump
            assert row.size == len(content2)

    db_run(check())


def test_generation_conflict_409_keeps_task(api, session_factory, db_run, make_user):
    """目标文件在任务期间被改名 → 完成 409（UPLOAD_CONFLICT），预留保留，不覆盖。"""
    user = make_user("alice")
    v1 = _complete_chunks(api, user, b"original")
    file_id = v1["file"]["id"]

    content2 = b"conflicting new bytes"
    init = _init(api, user, "f.txt", len(content2)).json()["data"]
    uid = init["upload_id"]
    for i in range(init["chunk_count"]):
        _part(api, user, uid, i + 1, content2[i * CHUNK:(i + 1) * CHUNK])

    # 任务期间目标被改名（generation 变化）
    api.patch(f"/api/files/{file_id}/rename", json={"new_name": "renamed.txt"}, headers=auth(user))

    resp = api.post(f"/api/uploads/{uid}/complete", headers=auth(user))
    assert resp.status_code == 409
    assert resp.json()["code"] == 3004

    async def check_kept():
        async with session_factory() as db:
            task = (await db.execute(
                select(UploadTask).where(UploadTask.upload_id == uid)
            )).scalar_one()
            assert task.status in ("UPLOADING", "VERIFYING", "CREATED")  # 限时任务保留
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes > 0  # 预留保留
            row = await db.get(UserFile, file_id)
            assert row.name == "renamed.txt"  # 未被覆盖

    db_run(check_kept())

    # 用户"另存新文件"：取消任务释放预留
    api.post(f"/api/uploads/{uid}/cancel", headers=auth(user))
    async def check_released():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.reserved_bytes == 0
    db_run(check_released())


def test_repeated_completes_single_version(api, session_factory, db_run, make_user):
    """完成幂等不变量：重复 complete 只有一个结果版本，全部返回同一 version_id。
    （真并发 T11 依赖 MySQL FOR UPDATE，安排在容器检查点执行；SQLite+StaticPool
     的线程共连场景无法承载并行事务。）"""
    user = make_user("alice")
    content = b"race" * 30  # 120B → 2 片
    init = _init(api, user, "race.txt", len(content), sha=_sha(content)).json()["data"]
    uid = init["upload_id"]
    for i in range(init["chunk_count"]):
        assert _part(api, user, uid, i + 1, content[i * CHUNK:(i + 1) * CHUNK]).status_code == 200

    results = [
        api.post(f"/api/uploads/{uid}/complete", headers=auth(user)).json()["data"]
        for _ in range(5)
    ]

    version_ids = {r["version_id"] for r in results}
    assert len(version_ids) == 1
    assert sum(1 for r in results if not r.get("duplicate")) == 1  # 恰一个真正提交

    async def check():
        async with session_factory() as db:
            usage = (await db.execute(select(QuotaUsage))).scalar_one()
            assert usage.used_bytes == len(content) and usage.reserved_bytes == 0

    db_run(check())


def test_outbox_rows_written_on_commit(api, session_factory, db_run, make_user):
    user = make_user("alice")
    result = _complete_chunks(api, user, b"outbox test")

    async def check():
        async with session_factory() as db:
            rows = (await db.execute(select(Outbox).order_by(Outbox.id))).scalars().all()
            assert any(r.event_type == "file.created" for r in rows)

    db_run(check())


def test_instant_upload_own_space_only(api, session_factory, db_run, make_user):
    """秒传：本人个人空间命中 → instant；跨用户仅凭 hash → 不命中（T13）。"""
    alice = make_user("alice")
    content = b"instant-target-content"
    first = _complete_chunks(api, alice, content)
    assert first["instant"] is False
    sha = _sha(content)

    # alice 再次上传同内容 → instant
    resp = _init(api, alice, "instant.txt", len(content), sha=sha)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["instant"] is True and data["upload_id"] is None
    assert data["file"]["size"] == len(content)

    # bob 仅知 hash → 不命中（不泄露存在性），走完整上传
    bob = make_user("bob")
    resp = _init(api, bob, "try-instant.txt", len(content), sha=sha)
    data = resp.json()["data"]
    assert data["instant"] is False

    async def check_refs():
        async with session_factory() as db:
            obj = (await db.execute(select(StorageObject))).scalars().all()
            assert all(o.ref_count >= 1 for o in obj)

    db_run(check_refs())
