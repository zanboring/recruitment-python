from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException
from app.models.user import User
from app.utils.security import verify_token

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    try:
        payload = verify_token(credentials.credentials)
    except Exception:
        raise AppException("请先登录", 401)

    user = await db.get(User, int(payload["sub"]))
    if not user:
        raise AppException("用户不存在", 401)

    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != "ADMIN":
        raise AppException("权限不足", 403)
    return user
