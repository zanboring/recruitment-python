"""岗位模块集成测试：增删改查 / 分页 / 统计 / 薪资预测 / 推荐 / 权限。

覆盖 routes: /api/jobs/*

**关于登录态**：`/api/jobs/*` 下的全部接口都要求登录（岗位数据属业务数据，
见 `app/routers/jobs.py` 的 router 级依赖）。下面三个查询/统计/推荐类
用类级 autouse fixture 统一注入登录态 —— 此前它们没带认证头也能通过，
是因为**接口当时漏了鉴权**（已修），而不是接口本就公开。
`TestJobCRUD` 不能这么做：它里面有「未登录应 401」「普通用户应 403」这类用例，
必须保留不带默认认证头的能力。
"""
import pytest
import pytest_asyncio

from tests.helpers import admin_token, auth_headers, create_job, register_and_login


@pytest.mark.asyncio
class TestJobCRUD:
    async def test_管理员创建岗位(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await create_job(client, token)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["title"] == "Java 后端开发工程师"
        assert data["job_key"].startswith("boss_")
        assert data["job_status"] == "ACTIVE"

    async def test_重复岗位被拒绝(self, client, db_session):
        token = await admin_token(client, db_session)
        assert (await create_job(client, token)).status_code == 200
        resp = await create_job(client, token)
        assert resp.status_code == 400
        assert resp.json()["message"] == "岗位已存在"

    async def test_普通用户不能创建岗位(self, client, db_session):
        user_token, _ = await register_and_login(client, "normal01")
        resp = await create_job(client, user_token)
        assert resp.status_code == 403
        assert resp.json()["message"] == "权限不足"

    async def test_未登录不能创建岗位(self, client):
        resp = await client.post("/api/jobs/", json={"title": "x", "source_site": "boss"})
        assert resp.status_code == 401

    async def test_更新岗位(self, client, db_session):
        token = await admin_token(client, db_session)
        job_id = (await create_job(client, token)).json()["data"]["id"]

        resp = await client.put(
            f"/api/jobs/{job_id}",
            json={"min_salary": 20000, "max_salary": 30000, "job_status": "OFFLINE"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert float(data["min_salary"]) == 20000
        assert data["job_status"] == "OFFLINE"

    async def test_更新不存在的岗位返回400(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await client.put(
            "/api/jobs/9999", json={"title": "nope"}, headers=auth_headers(token)
        )
        assert resp.status_code == 400

    async def test_删除岗位(self, client, db_session):
        token = await admin_token(client, db_session)
        job_id = (await create_job(client, token)).json()["data"]["id"]

        resp = await client.delete(f"/api/jobs/{job_id}", headers=auth_headers(token))
        assert resp.status_code == 200

        detail = await client.get(f"/api/jobs/{job_id}", headers=auth_headers(token))
        assert detail.status_code == 404

    async def test_查看详情不存在返回404(self, client, db_session):
        token = await admin_token(client, db_session)
        resp = await client.get("/api/jobs/404", headers=auth_headers(token))
        assert resp.status_code == 404
        assert resp.json()["message"] == "岗位不存在"

    async def test_查看详情返回正确数据(self, client, db_session):
        token = await admin_token(client, db_session)
        job_id = (await create_job(client, token)).json()["data"]["id"]

        resp = await client.get(f"/api/jobs/{job_id}", headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Java 后端开发工程师"


@pytest.mark.asyncio
class TestJobQuery:
    @pytest_asyncio.fixture(autouse=True)
    async def _logged_in(self, client, db_session):
        """本类用例都需登录态（岗位查询接口要求认证）。"""
        token = await admin_token(client, db_session)
        client.headers["Authorization"] = f"Bearer {token}"
        return token

    async def _seed(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token, title="Java 开发", city="长沙", skills="Java,SpringBoot")
        await create_job(client, token, title="Python 开发", company_name="乙公司", city="北京", skills="Python,Django")
        await create_job(client, token, title="Java 高级", company_name="丙公司", city="长沙", skills="Java,JVM")
        return token

    async def test_分页返回总数与列表(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"page_num": 1, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3
        assert len(data["list"]) == 2

    async def test_第二页返回剩余数据(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"page_num": 2, "page_size": 2})
        data = resp.json()["data"]
        assert len(data["list"]) == 1
        assert data["page_num"] == 2

    async def test_关键词命中标题或技能(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"keyword": "Java"})
        assert resp.json()["data"]["total"] == 2

    async def test_城市精确过滤(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"city": "北京"})
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["list"][0]["title"] == "Python 开发"

    async def test_公司名模糊匹配(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"company_name": "乙"})
        assert resp.json()["data"]["total"] == 1

    async def test_薪资区间过滤(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"min_salary": 25000})
        assert resp.json()["data"]["total"] == 0

    async def test_like转义避免通配符注入(self, client, db_session):
        """keyword 里的 % 必须被转义，否则会退化成全表匹配。"""
        await self._seed(client, db_session)
        resp = await client.post("/api/jobs/page", json={"keyword": "%"})
        assert resp.json()["data"]["total"] == 0


@pytest.mark.asyncio
class TestJobStats:
    @pytest_asyncio.fixture(autouse=True)
    async def _logged_in(self, client, db_session):
        """本类用例都需登录态（统计接口要求认证）。"""
        token = await admin_token(client, db_session)
        client.headers["Authorization"] = f"Bearer {token}"
        return token

    async def _seed(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token, city="长沙", min_salary=8000, max_salary=12000,
                         education="本科", skills="Java,SpringBoot")
        await create_job(client, token, title="Python 开发", company_name="乙公司", city="长沙",
                         min_salary=15000, max_salary=25000, education="硕士", skills="Python,Django")

    async def test_城市统计(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.get("/api/jobs/stat/city")
        data = resp.json()["data"]
        assert data[0] == {"name": "长沙", "count": 2}

    async def test_技能统计按出现次数降序(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/stat/skill")).json()["data"]
        mapping = {d["name"]: d["count"] for d in data}
        assert mapping["SpringBoot"] == 1
        assert mapping["Python"] == 1
        assert mapping["Django"] == 1
        counts = [d["count"] for d in data]
        assert counts == sorted(counts, reverse=True)

    async def test_薪资分布按中位数落到区间(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/stat/salary-range")).json()["data"]
        mapping = {d["name"]: d["count"] for d in data}
        # (8000+12000)/2 = 10000 -> 10k-15k； (15000+25000)/2 = 20000 -> 20k-30k
        assert mapping["10k-15k"] == 1
        assert mapping["20k-30k"] == 1
        assert mapping["5k-10k"] == 0

    async def test_学历统计(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/stat/education")).json()["data"]
        assert {d["name"] for d in data} == {"本科", "硕士"}

    async def test_汇总包含总量与均值(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/analysis/summary")).json()["data"]
        assert data["total"] == 2
        assert data["active"] == 2
        assert data["avg_salary"] > 0


@pytest.mark.asyncio
class TestRecommendAndSalary:
    @pytest_asyncio.fixture(autouse=True)
    async def _logged_in(self, client, db_session):
        """本类用例都需登录态（推荐与薪资预测接口要求认证）。"""
        token = await admin_token(client, db_session)
        client.headers["Authorization"] = f"Bearer {token}"
        return token

    async def _seed(self, client, db_session):
        token = await admin_token(client, db_session)
        await create_job(client, token, title="Java 开发", skills="Java,SpringBoot",
                         education="本科", experience="1-3年", city="长沙")
        await create_job(client, token, title="算法工程师", company_name="乙公司", skills="Java,Python,算法",
                         education="硕士", experience="3-5年", city="北京")

    async def test_推荐结果按分数降序(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.get("/api/jobs/recommend", params={"skills": "Java,SpringBoot"})
        data = resp.json()["data"]
        assert len(data) == 2
        scores = [d["score"] for d in data]
        assert scores == sorted(scores, reverse=True)
        # job1 技能 {java, spring} 与查询完全一致，相似度应最高
        assert data[0]["title"] == "Java 开发"

    async def test_推荐只返回命中的岗位(self, client, db_session):
        """查询词与任何岗位都不匹配时应返回空列表，而不是全部岗位。"""
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/recommend", params={"skills": "Kotlin"})).json()["data"]
        assert data == []

    async def test_推荐计算字段完整(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/recommend", params={"skills": "Java"})).json()["data"]
        item = data[0]
        for key in ("job_id", "score", "skill_similarity", "education_match", "experience_match"):
            assert key in item

    async def test_推荐按城市过滤(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/recommend", params={"city": "北京"})).json()["data"]
        assert len(data) == 1
        assert data[0]["city"] == "北京"

    async def test_智能推荐limit生效(self, client, db_session):
        await self._seed(client, db_session)
        resp = await client.post(
            "/api/jobs/recommend/intelligent",
            json={"skills": "Java", "experienceYears": 2, "limit": 1},
        )
        assert len(resp.json()["data"]) == 1

    async def test_学历不足时学历匹配度按比例打折(self, client, db_session):
        await self._seed(client, db_session)
        data = (await client.get("/api/jobs/recommend", params={"skills": "Java", "education": "本科"})).json()["data"]
        algo = next(d for d in data if d["title"] == "算法工程师")
        # 岗位要求硕士(4)，用户本科(3) -> 3/4
        assert abs(algo["education_match"] - 0.75) < 1e-6

    async def test_薪资预测一线城市高于新一线(self, client):
        beijing = (await client.get("/api/jobs/predict-salary", params={"city": "北京"})).json()["data"]
        changsha = (await client.get("/api/jobs/predict-salary", params={"city": "长沙"})).json()["data"]
        assert beijing["avg_salary"] > changsha["avg_salary"]

    async def test_薪资预测结果落在合理区间(self, client):
        data = (await client.get(
            "/api/jobs/predict-salary",
            params={"city": "长沙", "education": "本科", "experience": "1-3年", "skills": "Java"},
        )).json()["data"]
        assert 5000 <= data["avg_salary"] <= 100000
        assert data["min_salary"] < data["avg_salary"] < data["max_salary"]

    async def test_高薪技能产生溢价(self, client):
        plain = (await client.get("/api/jobs/predict-salary", params={"skills": "Java"})).json()["data"]
        premium = (await client.get(
            "/api/jobs/predict-salary", params={"skills": "算法,机器学习,深度学习"})).json()["data"]
        assert premium["skill_premium"] > plain["skill_premium"]
        assert premium["skill_premium"] <= 15


# ---------------------------------------------------------------------------
# 系统性防护：/api/jobs/* 下**全部**接口都必须要求登录
# ---------------------------------------------------------------------------
#
# 背景：此前本文件 16 个接口（列表 / 详情 / 7 个统计 / 分析 / 薪资预测 /
# 推荐 / 技能画像）**都没有认证依赖**，任何人无需登录即可读取岗位数据
# （实测 `POST /api/jobs/page` 返回 200 + 数据、`/api/jobs/predict-salary`
# 返回薪资区间）。而同项目其它接口都要求登录 —— 是遗漏而非设计。
#
# 修法是在 router 级声明 `dependencies=[Depends(get_current_user)]`，
# 一行覆盖全部现有接口与将来新增的接口。**但那一行很容易被误删**
# （比如有人在整理 import 时顺手删掉"未使用"的 Depends），
# 所以这里遍历全部路由做一次穷举断言。


@pytest.mark.asyncio
async def test_岗位接口全部要求登录(app, client):
    """无 token 访问 /api/jobs 下任何接口都不应放行。

    这条用例不针对某个接口，而是针对「鉴权覆盖」本身 ——
    它是防止未来新增接口再漏的第一道网。
    """
    targets = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/jobs"):
            targets.add(path)

    assert targets, "未枚举到 /api/jobs 路由，测试前提不成立"

    leaked = []
    for path in sorted(targets):
        # 把路径参数替换成占位值，避免 404 掩盖鉴权结果
        url = path.replace("{job_id}", "1").replace("{task_id}", "1")
        resp = await client.get(url)
        # 依赖在请求处理前执行 → 未登录应 401；
        # 允许 403（理论上不该出现）与 405（方法不匹配）之外的放行都算漏。
        if resp.status_code not in (401, 403, 405, 422):
            leaked.append((path, resp.status_code))

    assert not leaked, (
        "以下岗位接口未要求登录（任何人都能读取岗位数据）：\n  "
        + "\n  ".join(f"{p} → HTTP {c}" for p, c in leaked)
        + "\n请确认 app/routers/jobs.py 的 router 级 dependencies 仍在。"
    )
