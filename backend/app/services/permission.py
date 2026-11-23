"""权限解析：全项目唯一鉴权入口（CODING_PLAN §2）。

规则：
- PermissionResolver.get_role() 查 space_member，OWNER>ADMIN>EDITOR>VIEWER
- 非成员访问资源 → NOT_FOUND（404，不泄露存在性）
- 成员但角色不足 → FORBIDDEN（403，6001）
- 个人空间 = 单成员空间，注册时建 OWNER 成员
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ErrorCode
from app.models.space import (
    ROLE_OWNER,
    ROLE_VIEWER,
    SPACE_TYPE_PERSONAL,
    Space,
    SpaceMember,
)

ROLE_RANK = {"VIEWER": 1, "EDITOR": 2, "ADMIN": 3, "OWNER": 4}


class PermissionResolver:
    @staticmethod
    async def get_role(db: AsyncSession, user_id: int, space_id: int) -> str | None:
        result = await db.execute(
            select(SpaceMember.role).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
            )
        )
        return result.scalar_one_or_none()


async def ensure_role(
    db: AsyncSession, user_id: int, space_id: int, min_role: str = ROLE_VIEWER
) -> str:
    """资源接口的统一鉴权入口：非成员 404，角色不足 403。"""
    role = await PermissionResolver.get_role(db, user_id, space_id)
    if role is None:
        raise BizError(ErrorCode.NOT_FOUND)
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise BizError(ErrorCode.FORBIDDEN)
    return role


async def get_personal_space(db: AsyncSession, user_id: int) -> Space:
    """个人空间：注册事务内创建，正常必然存在；缺失属于不变量被破坏。"""
    space = (
        await db.execute(
            select(Space).where(Space.type == SPACE_TYPE_PERSONAL, Space.owner_id == user_id)
        )
    ).scalar_one_or_none()
    if space is None:
        raise BizError(ErrorCode.INTERNAL, message="个人空间缺失，请联系管理员", http_status=500)
    return space


__all__ = ["PermissionResolver", "ensure_role", "get_personal_space", "ROLE_RANK", "ROLE_OWNER"]
