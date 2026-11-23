"""文件名校验：路径穿越防线集中一处（CODING_PLAN §2），所有写入口必须先过这里。

规则：
- 长度 1..255，非空
- 禁止路径成分："/"、"\\"、".."、单独的 "."
- 禁止控制字符（<0x20 与 0x7F）与 NUL
- 禁止 Windows 保留字符 <>:"|?*
- 禁止结尾的点或空格（Windows 内核会静默剥离，造成"看起来不同实为同名"）
- 禁止保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9，含带扩展名形式）
"""
import re

from app.core.errors import BizError, ErrorCode

MAX_FILENAME = 255

_RESERVED_DEVICES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def validate_filename(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise BizError(ErrorCode.INVALID_FILENAME, message="文件名不能为空")
    if len(name) > MAX_FILENAME:
        raise BizError(ErrorCode.INVALID_FILENAME, message="文件名超过 255 字符")
    if name in (".", ".."):
        raise BizError(ErrorCode.INVALID_FILENAME, message="文件名不能为 . 或 ..")
    if _FORBIDDEN_CHARS.search(name):
        raise BizError(ErrorCode.INVALID_FILENAME, message="文件名包含非法字符（/ \\\\ : * ? \" < > | 或控制字符）")
    if name.endswith(".") or name.endswith(" "):
        raise BizError(ErrorCode.INVALID_FILENAME, message="文件名不能以点或空格结尾")
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_DEVICES:
        raise BizError(ErrorCode.INVALID_FILENAME, message=f"{stem} 是系统保留名")
    return name
