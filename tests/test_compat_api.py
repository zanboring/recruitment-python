"""Java 版前端兼容层回归测试（app/routers/compat.py）。

背景：配套前端 `recruitment-system-frontend` 按 Java 版契约调用
`/api/crawl/*`、`/api/users/*`、`/api/data/*`，Python 重构后这些路径全部 404，
导致爬取管理、数据管理、用户管理三个模块整体不可用。

兼容层补上别名路由与字段兼容后，必须把行为冻结住 —— 否则下一次重构
（或误删 compat.py）会让前端再次大面积失败，而且这种失败很隐蔽
（接口 404 / 页面显示 undefined），不跑前端根本发现不了。
"""
import pytest

from tests.helpers import admin_token, auth_headers, create_job, register_and_login


@pytest.fixture(autouse=True)
def _no_real_crawl(monkeypatch):
    """屏蔽真实后台爬取：单元测试不应去连目标站点。"""
    import app.routers.compat as compat

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(compat, "_run_crawl_in_background", _noop)


# ------------------------------------------------------------------ 爬取管理


@pytest.mark.asyncio
class TestCrawlCompat:
    async def test_创建任务返回任务ID(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await client.post(
            "/api/crawl/task",
            json={"sourceSite": "boss", "keyword": "Java", "city": "长沙"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json()["data"], int), "前端需要数字类型的任务 ID"

    async def test_多城市拆成多个任务(self, client, db_session):
        token = await admin_token(client, db_session)
        await client.post(
            "/api/crawl/task",
            json={"sourceSite": "boss", "keyword": "Java", "city": "长沙,北京,上海"},
            headers=auth_headers(token),
        )
        resp = await client.get("/api/crawl/tasks", headers=auth_headers(token))
        assert len(resp.json()["data"]) == 3

    async def test_任务字段与状态映射(self, client, db_session):
        """前端 TaskTable.vue 只认 PENDING/RUNNING/FINISHED/FAILED。"""
        token = await admin_token(client, db_session)
        await client.post(
            "/api/crawl/task",
            json={"sourceSite": "boss", "keyword": "Java", "city": "长沙"},
            headers=auth_headers(token),
        )
        task = (await client.get("/api/crawl/tasks", headers=auth_headers(token))).json()["data"][0]
        for field in ("id", "sourceSite", "keyword", "city", "status", "jobCount", "createdAt"):
            assert field in task, f"缺少前端依赖的字段 {field}"
        assert task["status"] in ("PENDING", "RUNNING", "FINISHED", "FAILED")

    async def test_启动与删除任务(self, client, db_session):
        token = await admin_token(client, db_session)
        await client.post(
            "/api/crawl/task",
            json={"sourceSite": "boss", "keyword": "Java", "city": "长沙"},
            headers=auth_headers(token),
        )
        task_id = (await client.get("/api/crawl/tasks", headers=auth_headers(token))).json()["data"][0]["id"]

        assert (await client.post(f"/api/crawl/task/{task_id}/start", headers=auth_headers(token))).status_code == 200
        assert (await client.delete(f"/api/crawl/task/{task_id}", headers=auth_headers(token))).status_code == 200
        assert (await client.get("/api/crawl/tasks", headers=auth_headers(token))).json()["data"] == []


# ------------------------------------------------------------------ 用户管理


@pytest.mark.asyncio
class TestUserCompat:
    async def test_用户列表返回分页包装(self, client, db_session):
        token = await admin_token(client, db_session)
        data = (await client.get("/api/users?pageNum=1&pageSize=10", headers=auth_headers(token))).json()["data"]
        for field in ("list", "total", "pageNum", "pageSize"):
            assert field in data, f"前端 UserManagement.vue 依赖 {field}"

    async def test_用户对象含启用状态(self, client, db_session):
        token = await admin_token(client, db_session)
        data = (await client.get("/api/users", headers=auth_headers(token))).json()["data"]
        assert "enabled" in data["list"][0]

    async def test_禁用用户后无法登录(self, client, db_session):
        token = await admin_token(client, db_session)
        await register_and_login(client, "to_disable")
        users = (await client.get("/api/users", headers=auth_headers(token))).json()["data"]["list"]
        target = [u for u in users if u["username"] == "to_disable"][0]

        resp = await client.patch(
            f"/api/users/{target['id']}/status?enabled=false", headers=auth_headers(token)
        )
        assert resp.status_code == 200, resp.text

        login = await client.post("/api/auth/login", json={"username": "to_disable", "password": "Pass@1234"})
        assert login.status_code == 401, "被禁用的账号不应能登录"

    async def test_不能禁用或删除自己(self, client, db_session):
        token = await admin_token(client, db_session)
        me = (await client.get("/api/users", headers=auth_headers(token))).json()["data"]["list"][0]

        assert (await client.patch(
            f"/api/users/{me['id']}/status?enabled=false", headers=auth_headers(token)
        )).status_code == 400
        assert (await client.delete(
            f"/api/users/{me['id']}", headers=auth_headers(token)
        )).status_code == 400

    async def test_修改用户资料(self, client, db_session):
        token = await admin_token(client, db_session)
        await register_and_login(client, "to_edit")
        users = (await client.get("/api/users", headers=auth_headers(token))).json()["data"]["list"]
        target = [u for u in users if u["username"] == "to_edit"][0]

        resp = await client.put(
            f"/api/users/{target['id']}",
            json={"email": "edited@example.com", "skills": "Java"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["email"] == "edited@example.com"


# ------------------------------------------------------------------ 字段命名兼容


@pytest.mark.asyncio
class TestCamelCaseCompat:
    async def test_响应同时保留两种字段命名(self, client, db_session):
        """前端按 camelCase 取字段，本服务原生是 snake_case，两者都要在。"""
        token = await admin_token(client, db_session)
        await create_job(client, token)
        item = (await client.post("/api/jobs/page", json={}, headers=auth_headers(token))).json()["data"]["list"][0]

        for snake, camel in [("job_key", "jobKey"), ("company_name", "companyName"),
                             ("min_salary", "minSalary"), ("created_at", "createdAt")]:
            assert snake in item, f"原生字段 {snake} 丢失"
            assert camel in item, f"前端依赖的 {camel} 缺失"

    async def test_请求参数接受camelCase(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token, company_name="甲公司")
        await create_job(client, token, title="另一个岗位", company_name="乙公司")

        resp = await client.post(
            "/api/jobs/page",
            json={"companyName": "甲", "pageNum": 1, "pageSize": 10},
            headers=auth_headers(token),
        )
        assert resp.json()["data"]["total"] == 1, "camelCase 的 companyName 过滤未生效"

    async def test_模型切换接受modelName(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await client.post("/api/model/switch", json={"modelName": "local"}, headers=auth_headers(token))
        assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text

    async def test_模型状态含前端所需字段(self, client, db_session):
        token = await admin_token(client, db_session)
        data = (await client.get("/api/model/status", headers=auth_headers(token))).json()["data"]
        for field in ("currentModel", "ollamaAvailable", "zhipuAvailable", "modelList"):
            assert field in data, f"ModelManager.vue 依赖 {field}"


# ------------------------------------------------------------------ 路径遮蔽回归


@pytest.mark.asyncio
class TestCompatRouteOrder:
    async def test_知识库preview不被详情路由吞掉(self, client, db_session):
        """`GET /api/knowledge/preview` 必须在 `/{knowledge_id}` 之前匹配。"""
        token = await admin_token(client, db_session)
        resp = await client.get("/api/knowledge/preview?question=测试", headers=auth_headers(token))
        assert resp.status_code == 200, f"被路由吞掉：{resp.status_code} {resp.text}"

    async def test_知识库状态与评分接口(self, client, db_session):
        token = await admin_token(client, db_session)
        created = await client.post(
            "/api/knowledge", json={"question": "问", "answer": "答"}, headers=auth_headers(token)
        )
        kid = created.json()["data"]["id"]

        assert (await client.put(f"/api/knowledge/{kid}/status?status=0", headers=auth_headers(token))).status_code == 200
        # 前端把 score 放在 body.params 里（axios.put 第二参数即请求体）
        assert (await client.put(
            f"/api/knowledge/{kid}/score", json={"params": {"score": 3}}, headers=auth_headers(token)
        )).status_code == 200


# ------------------------------------------------------------------ 数据管理


@pytest.mark.asyncio
class TestDataCompat:
    async def test_导出返回xlsx(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await client.get("/api/data/export", headers=auth_headers(token))
        assert resp.status_code == 200
        assert "spreadsheet" in resp.headers.get("content-type", "")

    async def test_清洗返回删除条数(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token)

        resp = await client.post("/api/data/cleanup", headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["data"] == 1, "应返回删除的岗位条数供前端提示"

        remaining = (await client.post("/api/jobs/page", json={}, headers=auth_headers(token))).json()["data"]["total"]
        assert remaining == 0


# ------------------------------------------------------------------ 认证兼容


@pytest.mark.asyncio
class TestAuthCompat:
    async def test_默认用户名接口(self, client, db_session):
        resp = await client.get("/api/auth/default-username")
        assert resp.status_code == 200
        assert resp.json()["data"]["username"]

    async def test_自动登录在非生产环境可用(self, client, db_session):
        await admin_token(client, db_session)  # 确保存在管理员
        resp = await client.post("/api/auth/auto-login")
        assert resp.status_code == 200
        body = resp.json()["data"]
        for field in ("id", "username", "role", "token"):
            assert field in body

    async def test_自动登录在生产环境被拒绝(self, client, db_session, monkeypatch):
        """不能留下「无需凭证即得管理员 Token」的后门。"""
        from app.config import settings

        monkeypatch.setattr(settings, "app_env", "production")
        resp = await client.post("/api/auth/auto-login")
        assert resp.status_code == 403, resp.text
