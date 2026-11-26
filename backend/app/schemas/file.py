"""目录/文件相关请求响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FileItemOut(BaseModel):
    id: int
    name: str
    is_folder: bool
    size: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateFolderRequest(BaseModel):
    parent_id: int = Field(default=0, ge=0, description="0=空间根")
    name: str = Field(min_length=1, max_length=255)


class RenameRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=255)


class UploadOut(FileItemOut):
    version_no: int
    storage_object_id: int
    sha256: str
    size: int


class QuotaOut(BaseModel):
    quota_bytes: int
    used_bytes: int
    remaining_bytes: int


class PageOut(BaseModel):
    items: list[FileItemOut]
    total: int
    page: int
    page_size: int


class PathItem(BaseModel):
    id: int
    name: str


class PathOut(BaseModel):
    items: list[PathItem]
