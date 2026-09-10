from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import AppException
from app.models.user import User
from app.services.auth_service import AuthService
from app.schemas.common import Result
from app.schemas.auth import LoginRequest, RegisterRequest, ChangePasswordRequest, LoginResponse, UserInfoResponse
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login")
@log_action("用户登录")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await AuthService.login(db, request)
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 401)


@router.post("/register")
@log_action("用户注册")
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await AuthService.register(db, request)
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 400)


@router.post("/change-password")
@log_action("修改密码")
async def change_password(
    request: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    try:
        await AuthService.change_password(db, user, request)
        return Result.success()
    except ValueError as e:
        raise AppException(str(e), 400)


@router.get("/user-info")
async def user_info(user: User = Depends(get_current_user)):
    result = AuthService.get_user_info(user)
    return Result.success(result)
