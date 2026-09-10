from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.exceptions import AppException
from app.models.user import User
from app.services.auth_service import AuthService
from app.schemas.common import Result
from app.schemas.user import UserUpdateRequest, UserResponse
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/user", tags=["用户"])


@router.put("/me")
@log_action("更新个人资料")
async def update_profile(
    request: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    try:
        result = await AuthService.update_profile(db, user, request)
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 400)


@router.get("/me")
async def get_profile(user: User = Depends(get_current_user)):
    return Result.success(UserResponse.model_validate(user))


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    user = await db.get(User, user_id)
    if not user:
        raise AppException("用户不存在", 404)
    return Result.success(UserResponse.model_validate(user))


@router.get("/")
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    from sqlalchemy import select
    stmt = select(User).order_by(User.created_at.desc())
    result = await db.execute(stmt)
    users = result.scalars().all()
    return Result.success([UserResponse.model_validate(user) for user in users])


@router.delete("/{user_id}")
@log_action("删除用户")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    if current_user.id == user_id:
        raise AppException("不能删除自己", 400)

    user = await db.get(User, user_id)
    if not user:
        raise AppException("用户不存在", 404)

    await db.delete(user)
    await db.commit()
    return Result.success()