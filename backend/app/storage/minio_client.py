"""MinIO 封装层：所有对象 IO 的唯一出口。

纪律（PLAN §2.1.1-2）：
- 同步 minio SDK 一律经 ThreadPoolExecutor 执行，禁止阻塞事件循环；
- 读路径必须流式（按块 yield），禁止整对象读入内存；multipart 写入留待 M6；
- 桶私有，浏览器永不直连，签名链接不外发。
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import AsyncIterator, BinaryIO, Optional

import urllib3
from minio import Minio

from app.core.config import Settings

logger = logging.getLogger(__name__)

_READ_CHUNK = 1024 * 1024  # 1MiB


class MinioStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket_formal: str,
        bucket_candidate: str,
        pool_workers: int = 16,
        call_timeout: float = 10.0,
    ) -> None:
        self.bucket_formal = bucket_formal
        self.bucket_candidate = bucket_candidate
        self._call_timeout = call_timeout
        # 显式 http_client：连接/读超时必须存在，禁止 urllib3 默认无限等待；
        # 重试交由上层（ensure_buckets / M6 分片重试）显式控制
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=3, read=60),
            retries=False,
            maxsize=max(pool_workers * 2, 8),
        )
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            http_client=http_client,
        )
        self._pool = ThreadPoolExecutor(max_workers=pool_workers, thread_name_prefix="minio")

    @classmethod
    def from_settings(cls, settings: Settings) -> "MinioStorage":
        return cls(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            bucket_formal=settings.minio_bucket_formal,
            bucket_candidate=settings.minio_bucket_candidate,
            pool_workers=settings.minio_pool_workers,
            call_timeout=settings.minio_call_timeout,
        )

    # ---------- 基础 ----------
    async def _run(self, fn, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, partial(fn, *args, **kwargs))

    async def bucket_exists(self, bucket: str) -> bool:
        return bool(
            await asyncio.wait_for(
                self._run(self._client.bucket_exists, bucket),
                timeout=self._call_timeout,
            )
        )

    async def ensure_buckets(self, retries: int = 1, delay: float = 0.0) -> None:
        """启动时创建 formal/candidate 两桶；每次尝试有硬超时，失败只告警不阻断启动（health 暴露）。"""
        for bucket in (self.bucket_formal, self.bucket_candidate):
            for attempt in range(1, max(retries, 1) + 1):
                try:
                    if not await self.bucket_exists(bucket):
                        await asyncio.wait_for(
                            self._run(self._client.make_bucket, bucket),
                            timeout=self._call_timeout,
                        )
                    logger.info("minio bucket ready", extra={"bucket": bucket})
                    break
                except Exception as exc:  # noqa: BLE001 - 启动期容错，交由 health 暴露
                    if attempt >= max(retries, 1):
                        logger.error(
                            "minio bucket init failed",
                            extra={"bucket": bucket, "error": str(exc)},
                        )
                    elif delay > 0:
                        await asyncio.sleep(delay)

    def close(self) -> None:
        self._pool.shutdown(wait=False)

    # ---------- 对象操作 ----------
    async def put_stream(
        self,
        bucket: str,
        key: str,
        fileobj: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        """写入已知长度的流（fileobj 为同步可读对象；异步源由服务层先落临时文件，M3 接入）。"""
        result = await self._run(
            self._client.put_object, bucket, key, fileobj, length, content_type=content_type
        )
        return str(result.etag or "")

    async def get_range(
        self,
        bucket: str,
        key: str,
        offset: int = 0,
        length: Optional[int] = None,
    ) -> AsyncIterator[bytes]:
        """流式读取（可带 Range）；返回异步字节块迭代器，禁止聚合整个对象。"""
        response = await self._run(
            self._client.get_object, bucket, key, offset=offset, length=length
        )
        try:
            while True:
                chunk = await self._run(response.read, _READ_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await self._run(response.close)
            await self._run(response.release_conn)

    async def stat(self, bucket: str, key: str) -> dict:
        st = await self._run(self._client.stat_object, bucket, key)
        return {
            "size": st.size,
            "etag": st.etag,
            "last_modified": st.last_modified.isoformat() if st.last_modified else None,
            "content_type": st.content_type,
        }

    async def delete(self, bucket: str, key: str) -> None:
        await self._run(self._client.remove_object, bucket, key)
