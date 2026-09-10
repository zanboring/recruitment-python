"""测试用例通用辅助函数。"""
from app.utils.security import hash_password
from app.models.user import User


def build_register_payload(username: str, password: str = "Pass@1234", email: str = None):
    return {
        "username": username,
        "password": password,
        "email": email or f"{username}@example.com",
    }


async def register(client, username: str, password: str = "Pass@1234", email: str = None):
    resp = await client.post("/api/auth/register", json=build_register_payload(username, password, email))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def login(client, username: str, password: str = "Pass@1234"):
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    return resp


async def register_and_login(client, username: str, password: str = "Pass@1234"):
    """注册 + 登录，返回 (token, user_info)。"""
    await register(client, username, password)
    resp = await login(client, username, password)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["token"], data["user"]


async def promote_to_admin(db_session, user_id: int):
    """把用户提升为管理员（直接改库，避免为造数据再写一个管理员注册接口）。"""
    user = await db_session.get(User, user_id)
    user.role = "ADMIN"
    await db_session.commit()
    return user


async def create_admin(db_session, username: str = "admin01", password: str = "Admin@1234"):
    """直接在库里创建管理员账号。"""
    from sqlalchemy import select

    result = await db_session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            username=username,
            password=hash_password(password),
            email=f"{username}@example.com",
            role="ADMIN",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
    return user, password


async def admin_token(client, db_session, username: str = "admin01"):
    await create_admin(db_session, username)
    resp = await login(client, username, "Admin@1234")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


SAMPLE_JOB = {
    "title": "Java 后端开发工程师",
    "company_name": "某某科技有限公司",
    "source_site": "boss",
    "city": "长沙",
    "experience": "1-3年",
    "education": "本科",
    "min_salary": 12000,
    "max_salary": 20000,
    "skills": "Java,SpringBoot,MySQL,Redis",
    "job_desc": "负责后端服务设计与开发，参与系统架构优化。",
}


async def create_job(client, token: str, **overrides):
    payload = dict(SAMPLE_JOB)
    payload.update(overrides)
    resp = await client.post("/api/jobs/", json=payload, headers=auth_headers(token))
    return resp
