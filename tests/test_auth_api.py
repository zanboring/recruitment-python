"""认证链路集成测试：注册 / 登录 / 鉴权 / 改密 / 账号锁定。

覆盖 routes: /api/auth/*
"""
import pytest

from app.utils.security import create_token, verify_token
from tests.helpers import (
    register, register_and_login, login, auth_headers, build_register_payload,
)


@pytest.mark.asyncio
class TestRegister:
    async def test_注册成功返回用户信息(self, client):
        data = await register(client, "zhangsan")
        assert data["username"] == "zhangsan"
        assert data["role"] == "USER"
        assert "password" not in data

    async def test_重复用户名被拒绝(self, client):
        await register(client, "zhangsan")
        resp = await client.post("/api/auth/register", json=build_register_payload("zhangsan"))
        assert resp.status_code == 400
        assert resp.json()["message"] == "用户名已存在"

    async def test_邮箱格式非法返回422(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"username": "lisi", "password": "Pass@1234", "email": "not-an-email"},
        )
        assert resp.status_code == 422

    async def test_注册后密码以哈希存储(self, client, db_session):
        from sqlalchemy import select
        from app.models.user import User

        await register(client, "wangwu")
        result = await db_session.execute(select(User).where(User.username == "wangwu"))
        user = result.scalar_one()
        assert user.password != "Pass@1234"
        # 当前方案是「SHA-256 预哈希 + bcrypt」，前缀用于区分改造前的历史哈希
        assert user.password.startswith("sha256$")
        # 哈希本身仍应是 bcrypt 产物（前缀之后以 $2 开头）
        assert user.password[len("sha256$"):].startswith("$2")


@pytest.mark.asyncio
class TestLogin:
    async def test_登录成功返回token(self, client):
        _, user = await register_and_login(client, "login01")
        assert user["username"] == "login01"

    async def test_密码错误返回401且不明说原因(self, client):
        await register(client, "login02")
        resp = await login(client, "login02", "wrong-password")
        assert resp.status_code == 401
        # 安全：不能区分「用户不存在」和「密码错误」
        assert resp.json()["message"] == "用户名或密码错误"

    async def test_用户不存在同样返回用户名或密码错误(self, client):
        resp = await login(client, "ghost", "whatever")
        assert resp.status_code == 401
        assert resp.json()["message"] == "用户名或密码错误"

    async def test_连续失败5次触发账号锁定(self, client, db_session):
        from sqlalchemy import select
        from app.models.user import User

        await register(client, "locker")
        for _ in range(5):
            resp = await login(client, "locker", "bad-password")
            assert resp.status_code == 401

        resp = await login(client, "locker", "Pass@1234")
        assert resp.status_code == 401
        assert "账号已锁定" in resp.json()["message"]

        result = await db_session.execute(select(User).where(User.username == "locker"))
        user = result.scalar_one()
        assert user.locked_until is not None

    async def test_登录成功后清空失败计数(self, client, db_session):
        from sqlalchemy import select
        from app.models.user import User

        await register(client, "cleaner")
        await login(client, "cleaner", "bad-password")
        await login(client, "cleaner", "Pass@1234")

        result = await db_session.execute(select(User).where(User.username == "cleaner"))
        user = result.scalar_one()
        assert user.login_fail_count == 0
        assert user.locked_until is None


@pytest.mark.asyncio
class TestAuthGuard:
    async def test_未携带token访问受保护接口返回401(self, client):
        resp = await client.get("/api/auth/user-info")
        assert resp.status_code == 401
        assert resp.json()["message"] == "请先登录"

    async def test_非法token返回401(self, client):
        resp = await client.get("/api/auth/user-info", headers=auth_headers("not.a.jwt"))
        assert resp.status_code == 401
        assert resp.json()["message"] == "请先登录"

    async def test_过期token返回401(self, client):
        from datetime import datetime, timedelta, timezone
        from jose import jwt
        from app.config import settings

        expired = jwt.encode(
            {
                "sub": "1",
                "username": "expired",
                "role": "USER",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        resp = await client.get("/api/auth/user-info", headers=auth_headers(expired))
        assert resp.status_code == 401

    async def test_token对应用户不存在返回401(self, client):
        token = create_token(99999, "ghost", "USER")
        resp = await client.get("/api/auth/user-info", headers=auth_headers(token))
        assert resp.status_code == 401
        assert resp.json()["message"] == "用户不存在"

    async def test_正常token可以访问受保护接口(self, client):
        token, user = await register_and_login(client, "guard01")
        resp = await client.get("/api/auth/user-info", headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["data"]["username"] == "guard01"

    async def test_token可反解出用户角色(self):
        payload = verify_token(create_token(7, "u7", "ADMIN"))
        assert payload["sub"] == "7"
        assert payload["role"] == "ADMIN"


@pytest.mark.asyncio
class TestChangePassword:
    async def test_旧密码错误时被拒绝(self, client):
        token, _ = await register_and_login(client, "pwd01")
        resp = await client.post(
            "/api/auth/change-password",
            json={"old_password": "wrong", "new_password": "NewPass@1234"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "旧密码错误"

    async def test_修改成功后新旧密码均可用校验(self, client):
        token, _ = await register_and_login(client, "pwd02")
        resp = await client.post(
            "/api/auth/change-password",
            json={"old_password": "Pass@1234", "new_password": "NewPass@1234"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200

        assert (await login(client, "pwd02", "NewPass@1234")).status_code == 200
        assert (await login(client, "pwd02", "Pass@1234")).status_code == 401

    async def test_修改密码需要登录(self, client):
        resp = await client.post(
            "/api/auth/change-password",
            json={"old_password": "Pass@1234", "new_password": "NewPass@1234"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_登录响应平铺为前端UserVO契约(client, db_session):
    """登录响应必须是平铺的 {id, username, role, email, token}。

    嵌套结构 {token, user: {...}} 会让前端把整个对象存进 user store，
    role getter 读到 undefined —— 路由守卫把管理员当普通用户，
    **所有 /admin/* 页面被静默重定向回首页**（无报错、无提示，极难发现）。
    同项目的 /auth/auto-login 返回的就是平铺结构，两个入口必须一致。
    """
    from tests.helpers import register

    await register(client, "flat_user")
    resp = await client.post(
        "/api/auth/login",
        json={"username": "flat_user", "password": "Pass@1234"},
    )
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    # 前端 UserVO 契约的五个字段全部在顶层
    for key in ("id", "username", "role", "token"):
        assert key in data, f"缺少契约字段 {key}"
    assert data["role"] == "USER"
    assert data["username"] == "flat_user"
    # 不能再出现嵌套的 user 字段
    assert "user" not in data
