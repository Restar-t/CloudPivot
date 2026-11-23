"""结构化 JSON 日志 + 请求上下文（trace_id / user_id / space_id / ip）。

审计类测试（T 系列"记录日志与定位原因"）的底座：
- 每个请求经 trace 中间件分配 trace_id（响应头回传 X-Trace-Id）；
- M1 起认证后补写 user_id / space_id；
- 所有日志一行一个 JSON 对象，便于检索与归档证据。
"""
import contextvars
import json
import logging
import sys
import time
from typing import Any, Optional

# 请求上下文（contextvars 天然隔离并发请求）
trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
user_id_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("user_id", default=None)
space_id_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("space_id", default=None)
client_ip_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_ip", default=None)

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 请求上下文字段（存在才输出）
        for key, var in (
            ("trace_id", trace_id_var),
            ("user_id", user_id_var),
            ("space_id", space_id_var),
            ("ip", client_ip_var),
        ):
            value = var.get()
            if value is not None:
                payload[key] = value
        # 业务附加字段：logger.info("...", extra={"action": "upload", ...})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_") and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn 自身的 handler 去掉，统一走 root 的 JSON 输出；
    # access log 由 trace 中间件统一打印，避免双份。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
