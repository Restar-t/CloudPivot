"""认证相关请求/响应模型。"""
import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_charset(cls, v: str) -> str:
        if not USERNAME_PATTERN.fullmatch(v):
            raise ValueError("用户名仅允许 3-32 位字母/数字/下划线/连字符")
        return v


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=128)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    quota_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access 有效期（秒）


class LoginResult(TokenPair):
    user: UserOut
