"""预览类型判定（M5.6）：魔数识别 + 白名单决策。

安全规则（PLAN §3.3-5 / §五.6）：
- 图片（魔数识别，不信扩展名）/ PDF → inline，带 CSP + nosniff；
- 文本（txt/md/csv/json/log/md）→ inline text/plain；大小超上限或非 UTF-8/GBK 可解码 → 强制下载；
- SVG/HTML 等脚本承载型 → 强制 attachment（XSS 防线，绝不 inline）；
- 其余一律 attachment（下载），不提供预览。
"""
import contextlib
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class PreviewDecision:
    disposition: str        # "inline" | "attachment"
    content_type: str       # 响应 Content-Type
    reason: str             # 供前端提示


_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"%PDF-", "application/pdf", "pdf"),
    (b"BM", "image/bmp", "bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon", "ico"),
)
_TEXT_EXTS = {"txt", "md", "csv", "json", "log"}
_SCRIPT_EXTS = {"svg", "html", "htm", "xhtml", "js", "mjs", "css"}  # 脚本承载型：绝不 inline


def sniff(riff_head: bytes) -> tuple[str, str] | None:
    """返回 (content_type, kind) 或 None。支持 WEBP（RIFF 容器）。"""
    if len(riff_head) >= 12 and riff_head[:4] == b"RIFF" and riff_head[8:12] == b"WEBP":
        return "image/webp", "webp"
    for magic, ctype, kind in _MAGIC:
        if riff_head.startswith(magic):
            return ctype, kind
    return None


def decide(filename: str, head: bytes, size: int, text_preview_ok: bool) -> PreviewDecision:
    """综合魔数 + 扩展名 + 大小编码，给出预览决策。

    顺序：脚本扩展名拦截 → 魔数识别（PLAN §3.3-5：图片按魔数，不信扩展名）→
    文本扩展名路径 → 其余下载。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in _SCRIPT_EXTS:
        return PreviewDecision("attachment", "application/octet-stream", "script-type-download-only")

    sniffed = sniff(head)
    if sniffed is not None:
        ctype, kind = sniffed
        return PreviewDecision("inline", ctype, kind)

    if ext in _TEXT_EXTS:
        max_bytes = get_settings().share_preview_text_max_bytes
        if size > max_bytes:
            return PreviewDecision("attachment", "application/octet-stream", "text-too-large")
        if not text_preview_ok:
            return PreviewDecision("attachment", "application/octet-stream", "unknown-encoding")
        return PreviewDecision("inline", "text/plain; charset=utf-8", "text")

    return PreviewDecision("attachment", "application/octet-stream", "preview-unsupported")


def try_decode_text(data: bytes) -> str | None:
    """UTF-8 严格 → GBK 严格 → None；含 NUL 字节直接判为二进制
    （GBK 覆盖面过宽，二进制序列常能"解码成功"，NUL 是更可靠的信号）。"""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    with contextlib.suppress(UnicodeDecodeError):
        return data.decode("gbk")
    return None
