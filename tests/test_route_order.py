"""路由注册顺序回归测试。

背景：FastAPI 按注册顺序匹配路由。动态路径参数路由若排在静态路由之前，
会「吞掉」静态路径 —— 例如 DELETE /api/jobs/batch 先命中 /api/jobs/{job_id}，
"batch" 被当作 int 解析后直接返回 422，接口彻底不可用。

项目里曾有三处同类事故：DELETE /api/jobs/batch、GET /api/knowledge/all、
GET /api/knowledge/stats。修复后必须冻结行为，因此这里用**真实 HTTP 请求**
验证（而不是断言路由表顺序 —— 那是实现细节，重构时易产生假失败）。
"""
import pytest

from tests.helpers import admin_token, auth_headers, create_job


@pytest.mark.asyncio
class TestStaticRoutesNotShadowed:
    async def test_批量删除岗位不被路径参数吞掉(self, client, db_session):
        """DELETE /api/jobs/batch 必须进入批量删除逻辑，而不是 422 int_parsing。"""
        token = await admin_token(client, db_session)
        h = auth_headers(token)

        job_ids = []
        for i in range(3):
            resp = await create_job(client, token, title=f"批量删除测试岗位{i}")
            job_ids.append(resp.json()["data"]["id"])

        resp = await client.request("DELETE", "/api/jobs/batch", json=job_ids, headers=h)
        assert resp.status_code == 200, f"批量删除被路由吞掉：{resp.status_code} {resp.text}"
        assert resp.json()["code"] == 0

        # 确认真的删掉了（岗位详情接口要求登录，需带认证头）
        for job_id in job_ids:
            assert (await client.get(f"/api/jobs/{job_id}", headers=h)).status_code == 404

    async def test_知识库all与stats不被路径参数吞掉(self, client, db_session):
        """GET /api/knowledge/all 与 /stats 是静态路径，不能被 /{knowledge_id} 抢先匹配。"""
        token = await admin_token(client, db_session)
        h = auth_headers(token)

        for path in ("/api/knowledge/all", "/api/knowledge/stats"):
            resp = await client.get(path, headers=h)
            assert resp.status_code == 200, f"{path} 被路由吞掉：{resp.status_code} {resp.text}"
            assert resp.json()["code"] == 0

    async def test_知识库详情路径仍正常工作(self, client, db_session):
        """修顺序不能把动态路由本身改坏。"""
        token = await admin_token(client, db_session)
        h = auth_headers(token)

        created = await client.post(
            "/api/knowledge", json={"question": "测试问题", "answer": "测试答案"}, headers=h
        )
        assert created.status_code == 200, created.text
        knowledge_id = created.json()["data"]["id"]

        resp = await client.get(f"/api/knowledge/{knowledge_id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == knowledge_id

    async def test_岗位详情路径仍正常工作(self, client, db_session):
        token = await admin_token(client, db_session)
        job_id = (await create_job(client, token)).json()["data"]["id"]

        resp = await client.get(f"/api/jobs/{job_id}", headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == job_id
