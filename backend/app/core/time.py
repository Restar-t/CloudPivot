"""时间工具：全项目统一存 UTC naive（MySQL DateTime 不带时区）。"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
