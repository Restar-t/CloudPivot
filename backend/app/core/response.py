"""统一响应包 {code, message, data}（code=0 成功）。

约定：
- 路由正常返回业务数据（dict / Pydantic 模型 / `ok()` 的产物），ApiRoute 负责包壳；
- 文件流等 StreamingResponse / 已经是错误包的响应原样透传；
- 错误路径全部走全局异常处理器，见 main.py。
"""
import json
from typing import Any, Optional, TypeVar

from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response

T = TypeVar("T")

ENVELOPE_KEYS = {"code", "message"}


def ok(data: Any = None, message: str = "ok") -> dict:
    """显式构造成功包；与 ApiRoute 自动包壳等价，路由里二选一即可。"""
    return {"code": 0, "message": message, "data": data}


def error_body(code: int, message: str, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}


class ApiRoute(APIRoute):
    """自动包壳路由：凡是 2xx 且不是响应包/流式响应的结果，统一包成 {code,message,data}。"""

    def get_route_handler(self):  # type: ignore[override]
        original = super().get_route_handler()

        async def custom_handler(request) -> Response:
            response = await original(request)
            if not isinstance(response, JSONResponse) or not (200 <= response.status_code < 300):
                return response
            try:
                body = json.loads(response.body)
            except (ValueError, UnicodeDecodeError):
                return response
            if isinstance(body, dict) and ENVELOPE_KEYS.issubset(body.keys()):
                return response  # 已是响应包（ok() 或错误处理器产物）
            return JSONResponse(
                content=ok(body),
                status_code=response.status_code,
                background=response.background,
            )

        return custom_handler
