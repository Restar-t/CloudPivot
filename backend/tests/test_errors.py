"""错误码表完整性：分段正确、无重复、每个码都有文案与 HTTP 映射。"""
from app.core.errors import _HTTP_STATUS, _MESSAGES, BizError, ErrorCode


def test_codes_unique_and_ranged():
    codes = [int(c) for c in ErrorCode]
    assert len(codes) == len(set(codes))
    assert codes[0] == 0
    for c in codes[1:]:
        segment = c // 1000
        assert 1 <= segment <= 9, c


def test_every_code_has_message_and_status():
    for code in ErrorCode:
        if code == ErrorCode.SUCCESS:
            continue
        assert code in _MESSAGES, f"{code} 缺文案"
        assert code in _HTTP_STATUS, f"{code} 缺 HTTP 映射"


def test_biz_error_defaults():
    err = BizError(ErrorCode.QUOTA_EXCEEDED)
    assert err.message == "空间不足"
    assert err.http_status == 507
    assert int(err.code) == 5001


def test_biz_error_overrides():
    err = BizError(ErrorCode.NOT_FOUND, message="文件不存在", http_status=404, data={"id": 7})
    assert err.message == "文件不存在"
    assert err.data == {"id": 7}
