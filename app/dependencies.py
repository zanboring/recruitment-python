from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException
from app.models.user import User
from app.utils.security import verify_token

# auto_error=False：让「缺少 Authorization 头」也走 AppException 返回 401，
# 而不是 Starlette 默认的 403，保证未登录场景的错误码统一。
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    if credentials is None:
        raise AppException("请先登录", 401)

    try:
        payload = verify_token(credentials.credentials)
    except Exception:
        raise AppException("请先登录", 401)

    user = await db.get(User, int(payload["sub"]))
    if not user:
        raise AppException("用户不存在", 401)

    # 被管理员禁用的账号，已签发的 Token 也应立即失效
    if getattr(user, "enabled", True) is False:
        raise AppException("账号已被禁用", 401)

    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != "ADMIN":
        raise AppException("权限不足", 403)
    return user
