"""登录失败锁定测试。

回归的问题：``login_fail_count`` 只在**登录成功**时清零，于是一旦触发过锁定，
锁定期结束后计数仍停在阈值上 —— 用户只要再输错**一次**就立刻被再次锁定 30 分钟。

实测表现为「每 30 分钟只能试一次」：正常用户打错一个字母就会被反复锁死，
而且连输对密码的机会都没有。
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.user import User
from app.schemas.auth import LoginRequest
from app.services.auth_service import AuthService
from app.utils.security import hash_password
from app.utils.timeutil import utc_now

PASSWORD = "Right@123"
USERNAME = "lockuser"


@pytest.fixture
async def db(db_session):
    db_session.add(User(username=USERNAME, password=hash_password(PASSWORD), role="USER"))
    await db_session.commit()
    return db_session


async def _login(db, password: str) -> str:
    """返回 '成功' 或错误消息。"""
    try:
        await AuthService.login(db, LoginRequest(username=USERNAME, password=password))
        return "成功"
    except ValueError as e:
        return str(e)


async def _record(db) -> User:
    return (await db.execute(select(User).where(User.username == USERNAME))).scalar_one()


async def _expire_lock(db) -> None:
    """把锁定到期时间拨到过去，模拟锁定期已过。"""
    record = await _record(db)
    record.locked_until = utc_now() - timedelta(minutes=1)
    await db.commit()


@pytest.mark.asyncio
async def test_连续输错达阈值后账号被锁定(db):
    for _ in range(4):
        assert await _login(db, "wrong") == "用户名或密码错误"

    await _login(db, "wrong")           # 第 5 次触发锁定
    assert (await _record(db)).locked_until is not None


@pytest.mark.asyncio
async def test_锁定期间密码正确也无法登录(db):
    for _ in range(5):
        await _login(db, "wrong")

    assert "已锁定" in await _login(db, PASSWORD)


@pytest.mark.asyncio
async def test_锁定期结束后恢复完整的重试次数(db):
    """回归核心：过锁后不应「一错就再锁」。

    原先计数停在阈值上，过锁后第一次输错就立刻再锁 30 分钟 ——
    这里断言连续几次输错都只是普通报错，不会立刻又被锁。
    """
    for _ in range(5):
        await _login(db, "wrong")
    await _expire_lock(db)

    assert await _login(db, "wrong") == "用户名或密码错误"
    assert await _login(db, "wrong") == "用户名或密码错误"
    assert await _login(db, "wrong") == "用户名或密码错误"


@pytest.mark.asyncio
async def test_锁定期结束后正确密码可以登录(db):
    for _ in range(5):
        await _login(db, "wrong")
    await _expire_lock(db)

    assert await _login(db, PASSWORD) == "成功"


@pytest.mark.asyncio
async def test_登录成功后计数与锁定状态被清空(db):
    for _ in range(3):
        await _login(db, "wrong")

    assert await _login(db, PASSWORD) == "成功"

    record = await _record(db)
    assert record.login_fail_count == 0
    assert record.locked_until is None


@pytest.mark.asyncio
async def test_锁定提示的剩余分钟数向上取整(db):
    """剩余不足 1 分钟时不应显示「请0分钟后再试」。"""
    for _ in range(5):
        await _login(db, "wrong")

    record = await _record(db)
    record.locked_until = utc_now() + timedelta(seconds=20)
    await db.commit()
    assert "请1分钟后再试" in await _login(db, PASSWORD)

    record = await _record(db)
    record.locked_until = utc_now() + timedelta(minutes=5)
    await db.commit()
    assert "请5分钟后再试" in await _login(db, PASSWORD)
