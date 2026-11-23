"""M1 认证测试：夹具复用根 conftest 的 api/session_factory。"""
import pytest


@pytest.fixture()
def client(api):
    yield api
