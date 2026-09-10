import math
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.timeutil import utc_now

from app.models.user import User
from app.utils.security import hash_password, verify_password, create_token
from app.schemas.auth import LoginRequest, RegisterRequest, ChangePasswordRequest, LoginResponse, UserInfoResponse
from app.schemas.user import UserUpdateRequest


class AuthService:
    @staticmethod
    async def login(db: AsyncSession, request: LoginRequest) -> LoginResponse:
        stmt = select(User).where(User.username == request.username)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("用户名或密码错误")

        now = utc_now()
        if user.locked_until and user.locked_until > now:
            # 向上取整：否则剩余不足 1 分钟时会提示「请0分钟后再试」
            remaining = max(1, math.ceil((user.locked_until - now).total_seconds() / 60))
            raise ValueError(f"账号已锁定，请{remaining}分钟后再试")

        # 锁定期已过 → 重置失败计数，让用户重新获得完整尝试次数。
        #
        # 原先只在「登录成功」时清零计数，导致计数长期停在阈值上：锁定期一结束，
        # 用户只要再输错**一次**就立刻被再次锁定 30 分钟。实测下来等价于
        # 「每 30 分钟只能试一次」，正常用户打错一个字母就会被反复锁死。
        if user.locked_until and user.locked_until <= now:
            user.login_fail_count = 0
            user.locked_until = None
            await db.commit()

        if not verify_password(request.password, user.password):
            user.login_fail_count = (user.login_fail_count or 0) + 1
            if user.login_fail_count >= 5:
                user.locked_until = utc_now() + timedelta(minutes=30)
            await db.commit()
            raise ValueError("用户名或密码错误")

        # 密码正确后再检查启用状态：顺序反了会把「账号是否存在」泄露给未认证者
        if getattr(user, "enabled", True) is False:
            raise ValueError("账号已被禁用，请联系管理员")

        user.login_fail_count = 0
        user.locked_until = None
        await db.commit()

        token = create_token(user.id, user.username, user.role)
        user_info = UserInfoResponse.model_validate(user)
        return LoginResponse(token=token, user=user_info)

    @staticmethod
    async def register(db: AsyncSession, request: RegisterRequest) -> UserInfoResponse:
        stmt = select(User).where(User.username == request.username)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError("用户名已存在")

        hashed_pwd = hash_password(request.password)
        user = User(
            username=request.username,
            password=hashed_pwd,
            email=request.email,
            role="USER"
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        return UserInfoResponse.model_validate(user)

    @staticmethod
    async def change_password(db: AsyncSession, user: User, request: ChangePasswordRequest):
        if not verify_password(request.old_password, user.password):
            raise ValueError("旧密码错误")

        user.password = hash_password(request.new_password)
        await db.commit()

    @staticmethod
    def get_user_info(user: User) -> UserInfoResponse:
        return UserInfoResponse.model_validate(user)

    @staticmethod
    async def update_profile(db: AsyncSession, user: User, request: UserUpdateRequest) -> UserInfoResponse:
        if request.email is not None:
            user.email = request.email
        if request.skills is not None:
            user.skills = request.skills
        if request.education is not None:
            user.education = request.education
        if request.experience_years is not None:
            user.experience_years = request.experience_years

        await db.commit()
        await db.refresh(user)

        return UserInfoResponse.model_validate(user)
