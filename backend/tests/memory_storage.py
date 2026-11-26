"""MinioStorage 测试替身：内存字节存储，接口与 app.storage.minio_client.MinioStorage 一致。"""
import hashlib
from typing import AsyncIterator, BinaryIO, Optional


class MemoryStorage:
    def __init__(self) -> None:
        self.bucket_formal = "netdisk-formal"
        self.bucket_candidate = "netdisk-candidate"
        self.objects: dict[tuple[str, str], bytes] = {}
        self.deleted: list[tuple[str, str]] = []

    async def bucket_exists(self, bucket: str) -> bool:
        return True

    async def ensure_buckets(self, retries: int = 1, delay: float = 0.0) -> None:
        return None

    def close(self) -> None:
        return None

    async def put_stream(
        self, bucket: str, key: str, fileobj: BinaryIO, length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        data = fileobj.read(length)
        self.objects[(bucket, key)] = data
        return hashlib.sha256(data).hexdigest()

    async def get_range(
        self, bucket: str, key: str, offset: int = 0, length: Optional[int] = None,
    ) -> AsyncIterator[bytes]:
        data = self.objects.get((bucket, key), b"")
        if length is None:
            chunk = data[offset:]
        else:
            chunk = data[offset:offset + length]
        if chunk:
            yield chunk

    async def stat(self, bucket: str, key: str) -> dict:
        data = self.objects[(bucket, key)]  # 缺失时 KeyError → 测试快速失败
        return {"size": len(data), "etag": hashlib.sha256(data).hexdigest(),
                "last_modified": None, "content_type": "application/octet-stream"}

    async def delete(self, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))
        self.objects.pop((bucket, key), None)
