"""模型汇总：alembic autogenerate 与 metadata 收集的唯一 import 点。"""
from app.models.file_version import FileVersion
from app.models.login_log import LoginLog
from app.models.operation_log import (
    ACTION_DELETE,
    ACTION_DOWNLOAD,
    ACTION_FOLDER_CREATE,
    ACTION_RENAME,
    ACTION_UPLOAD,
    OperationLog,
)
from app.models.quota_usage import QuotaUsage
from app.models.refresh_session import RefreshSession
from app.models.space import ROLE_ADMIN, ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER, Space, SpaceMember
from app.models.storage_object import (
    OBJECT_STATUS_ACTIVE,
    OBJECT_STATUS_DELETED,
    OBJECT_STATUS_DELETING,
    StorageObject,
)
from app.models.user import User
from app.models.user_file import (
    FILE_STATUS_DELETED,
    FILE_STATUS_NORMAL,
    FILE_STATUS_TRASH,
    ROOT_PARENT_ID,
    UserFile,
)

__all__ = [
    "User", "LoginLog", "RefreshSession",
    "Space", "SpaceMember",
    "ROLE_OWNER", "ROLE_ADMIN", "ROLE_EDITOR", "ROLE_VIEWER",
    "UserFile", "ROOT_PARENT_ID",
    "FILE_STATUS_NORMAL", "FILE_STATUS_TRASH", "FILE_STATUS_DELETED",
    "StorageObject", "OBJECT_STATUS_ACTIVE", "OBJECT_STATUS_DELETING", "OBJECT_STATUS_DELETED",
    "FileVersion", "QuotaUsage", "OperationLog",
    "ACTION_UPLOAD", "ACTION_DOWNLOAD", "ACTION_DELETE", "ACTION_RENAME", "ACTION_FOLDER_CREATE",
]
