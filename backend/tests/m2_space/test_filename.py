"""M2.5 文件名工具单测：穿越串、非法字符、超长、保留名、合法样例。"""
import pytest

from app.core.errors import BizError, ErrorCode
from app.core.filename import validate_filename


@pytest.mark.parametrize("bad", [
    "", ".", "..", "a/b", "a\\b", "a\x00b", "a\nb", "a\x01b",
    "CON", "con", "CON.txt", "NUL.log", "COM1", "lpt9.tar.gz",
    "bad*", 'q?"x', "<html>", "a|b", "colon:name", "ends.",
    "ends ", "x" * 256,
])
def test_invalid_filenames(bad):
    with pytest.raises(BizError) as exc_info:
        validate_filename(bad)
    assert exc_info.value.code == ErrorCode.INVALID_FILENAME
    assert exc_info.value.http_status == 400


@pytest.mark.parametrize("good", [
    "正常文件夹", "report 2026.pdf", "a.b.c.txt", "file(1).jpg", "ünïcode", "x" * 255,
    ".hidden", "-leading-dash", " Backup.tar.gz",
])
def test_valid_filenames(good):
    assert validate_filename(good) == good


def test_traversal_variants_rejected():
    for name in ["..", "../etc/passwd", "..\\windows", "....", "a..b/../../c"]:
        with pytest.raises(BizError):
            validate_filename(name)
