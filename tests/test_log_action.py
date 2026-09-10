"""操作日志装饰器（@log_action）行为测试。

背景：`app/utils/log_decorator.py` 曾经「只有定义、零调用」—— 全项目没有任何
写入 sys_log 的代码路径，日志模块只能查空表和清空表，六大模块中的
「系统管理 / 操作日志」实际无法演示。

这组用例锁死三件事：
1. 关键写操作（登录 / 注册 / 新增岗位）确实写入了 sys_log；
2. 失败操作也留痕，且 success=0 并带上错误原因；
3. 密码、Token 这类敏感参数不落明文。
"""
import pytest

from tests.helpers import admin_token, auth_headers, create_job, register_and_login


@pytest.mark.asyncio
class TestLogActionWritten:
    async def test_登录与注册写入操作日志(self, client, db_session):
        await register_and_login(client, "loguser_a")
        token = await admin_token(client, db_session)

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        actions = [item["action"] for item in data["list"]]

        assert "用户注册" in actions
        assert "用户登录" in actions

    async def test_新增岗位写入操作日志(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token)

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        actions = [item["action"] for item in data["list"]]
        assert "新增岗位" in actions

    async def test_登录失败也留痕且标记失败(self, client, db_session):
        token = await admin_token(client, db_session)
        await client.post("/api/auth/login", json={"username": "ghost", "password": "wrong"})

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        failed = [item for item in data["list"] if item["success"] == 0]

        assert failed, "登录失败必须留下失败日志"
        assert failed[0]["error_msg"], "失败日志应记录错误原因"

    async def test_日志带真实请求来源(self, client, db_session):
        """uri / ip 不能为空 —— 这正是引入 RequestContextMiddleware 要解决的问题。"""
        await register_and_login(client, "loguser_b")
        token = await admin_token(client, db_session)

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        login_logs = [item for item in data["list"] if item["action"] == "用户登录"]
        assert login_logs, "应存在登录日志"

        entry = login_logs[0]
        assert entry["uri"] == "/api/auth/login"
        assert entry["ip"], "客户端 IP 不应为空"

    async def test_操作者用户名被正确记录(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token)

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        job_logs = [item for item in data["list"] if item["action"] == "新增岗位"]
        assert job_logs
        # admin_token 创建的账号是 admin01
        assert job_logs[0]["username"] == "admin01"


@pytest.mark.asyncio
class TestLogActionMasking:
    async def test_密码与令牌不落明文(self, client, db_session):
        """注册/登录/建管理员都用了默认密码，日志里绝不能出现这些明文。"""
        await register_and_login(client, "maskuser")  # 密码 Pass@1234
        token = await admin_token(client, db_session)  # 密码 Admin@1234

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]

        assert data["list"], "应有日志产生"
        for item in data["list"]:
            params = item["params"] or ""
            assert "Pass@1234" not in params, f"密码泄漏进日志：{params}"
            assert "Admin@1234" not in params, f"密码泄漏进日志：{params}"
            assert "Bearer " not in params, f"Token 泄漏进日志：{params}"

    async def test_不可序列化参数不会写坏日志(self, client, db_session):
        """db(AsyncSession)、Request 等对象只留类型名，不能让日志写入抛异常。"""
        token = await admin_token(client, db_session)
        await create_job(client, token)

        data = (
            await client.get("/api/logs/list?page_size=50", headers=auth_headers(token))
        ).json()["data"]
        job_logs = [item for item in data["list"] if item["action"] == "新增岗位"]
        assert job_logs
        assert "<AsyncSession>" in job_logs[0]["params"]
