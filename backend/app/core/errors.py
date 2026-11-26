"""错误码表与业务异常。

分段规则（CODING_PLAN §2）：
  0     成功
  1xxx  认证与会话
  2xxx  文件与目录
  3xxx  上传
  4xxx  分享
  5xxx  配额
  6xxx  权限
  9xxx  系统

所有错误信息用中文，作为前端"明确结果提示"的载体；http_status 为默认映射，
个别场景可由 BizError 调用方覆盖。
"""
from enum import IntEnum
from typing import Optional


class ErrorCode(IntEnum):
    SUCCESS = 0

    # ---- 1xxx 认证与会话 ----
    UNAUTHENTICATED = 1001       # 未认证 / 令牌无效
    TOKEN_EXPIRED = 1002         # 令牌已过期
    SESSION_REVOKED = 1003       # 会话已撤销（退出/改密）
    BAD_CREDENTIALS = 1004       # 用户名或密码错误
    LOGIN_LOCKED = 1005          # 失败次数过多，暂时锁定
    USERNAME_EXISTS = 1006       # 用户名已存在

    # ---- 2xxx 文件与目录 ----
    NOT_FOUND = 2001             # 资源不存在（对外统一隐藏存在性）
    NAME_CONFLICT = 2002         # 同目录活跃重名
    INVALID_FILENAME = 2003      # 非法文件名（穿越/控制字符/超长/保留名）
    FOLDER_CYCLE = 2004          # 移动目标构成循环
    STATE_CONFLICT = 2005        # 状态冲突（终态任务 / 当前版本等）
    RANGE_NOT_SATISFIABLE = 2006  # Range 起点越界（HTTP 416）

    # ---- 3xxx 上传 ----
    FILE_TOO_LARGE = 3001        # 超过单文件大小上限
    TYPE_NOT_ALLOWED = 3002      # 类型不在白名单
    CHECKSUM_MISMATCH = 3003     # SHA-256 / 大小核验失败
    UPLOAD_CONFLICT = 3004       # 同名冲突 / generation 变化 / 任务状态不允许

    # ---- 4xxx 分享 ----
    SHARE_NOT_FOUND = 4001       # 分享不存在或已取消（防枚举，统一口径）
    EXTRACT_CODE_WRONG = 4002    # 提取码错误
    SHARE_EXHAUSTED = 4003       # 访问次数用尽
    SHARE_EXPIRED = 4004         # 分享已过期

    # ---- 5xxx 配额 ----
    QUOTA_EXCEEDED = 5001        # 空间不足

    # ---- 6xxx 权限 ----
    FORBIDDEN = 6001             # 无权限

    # ---- 9xxx 系统 ----
    BAD_REQUEST = 9001           # 参数校验失败
    ROUTE_NOT_FOUND = 9002       # 路由不存在
    METHOD_NOT_ALLOWED = 9003    # 方法不允许
    DEPENDENCY_UNAVAILABLE = 9004  # 依赖服务不可用
    INTERNAL = 9999              # 未预期内部错误


_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SUCCESS: "ok",
    ErrorCode.UNAUTHENTICATED: "未认证或令牌无效",
    ErrorCode.TOKEN_EXPIRED: "登录已过期，请重新登录",
    ErrorCode.SESSION_REVOKED: "会话已失效，请重新登录",
    ErrorCode.BAD_CREDENTIALS: "用户名或密码错误",
    ErrorCode.LOGIN_LOCKED: "失败次数过多，请稍后再试",
    ErrorCode.USERNAME_EXISTS: "用户名已存在",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.NAME_CONFLICT: "同名资源已存在",
    ErrorCode.INVALID_FILENAME: "文件名不合法",
    ErrorCode.FOLDER_CYCLE: "不能移动到自身或其子目录",
    ErrorCode.STATE_CONFLICT: "当前状态不允许该操作",
    ErrorCode.RANGE_NOT_SATISFIABLE: "请求范围不满足",
    ErrorCode.FILE_TOO_LARGE: "文件超过大小上限",
    ErrorCode.TYPE_NOT_ALLOWED: "文件类型不允许上传",
    ErrorCode.CHECKSUM_MISMATCH: "内容校验失败，请重新上传",
    ErrorCode.UPLOAD_CONFLICT: "上传冲突，请刷新后重试",
    ErrorCode.SHARE_NOT_FOUND: "分享不存在或已失效",
    ErrorCode.EXTRACT_CODE_WRONG: "提取码错误",
    ErrorCode.SHARE_EXHAUSTED: "分享访问次数已用尽",
    ErrorCode.SHARE_EXPIRED: "分享已过期",
    ErrorCode.QUOTA_EXCEEDED: "空间不足",
    ErrorCode.FORBIDDEN: "没有权限执行该操作",
    ErrorCode.BAD_REQUEST: "请求参数错误",
    ErrorCode.ROUTE_NOT_FOUND: "请求的资源不存在",
    ErrorCode.METHOD_NOT_ALLOWED: "请求方法不被允许",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "服务暂不可用，请稍后重试",
    ErrorCode.INTERNAL: "服务内部错误",
}

_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.SESSION_REVOKED: 401,
    ErrorCode.BAD_CREDENTIALS: 401,
    ErrorCode.LOGIN_LOCKED: 429,
    ErrorCode.USERNAME_EXISTS: 409,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.NAME_CONFLICT: 409,
    ErrorCode.INVALID_FILENAME: 400,
    ErrorCode.FOLDER_CYCLE: 409,
    ErrorCode.STATE_CONFLICT: 409,
    ErrorCode.RANGE_NOT_SATISFIABLE: 416,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.TYPE_NOT_ALLOWED: 415,
    ErrorCode.CHECKSUM_MISMATCH: 422,
    ErrorCode.UPLOAD_CONFLICT: 409,
    ErrorCode.SHARE_NOT_FOUND: 404,
    ErrorCode.EXTRACT_CODE_WRONG: 401,
    ErrorCode.SHARE_EXHAUSTED: 403,
    ErrorCode.SHARE_EXPIRED: 404,
    ErrorCode.QUOTA_EXCEEDED: 507,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.BAD_REQUEST: 422,
    ErrorCode.ROUTE_NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}


class BizError(Exception):
    """业务异常：由全局异常处理器统一转为响应包，业务代码只 raise 不拼响应。"""

    def __init__(
        self,
        code: ErrorCode,
        message: Optional[str] = None,
        http_status: Optional[int] = None,
        data=None,
    ) -> None:
        self.code = code
        self.message = message or _MESSAGES.get(code, "未知错误")
        self.http_status = http_status or _HTTP_STATUS.get(code, 400)
        self.data = data
        super().__init__(f"[{int(code)}] {self.message}")
