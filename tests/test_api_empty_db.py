"""空库边界回归（E1）：job 表 0 行时，对外接口必须返回合法空值，不得 5xx。

背景：演示库刚被清空（job 0 行 / company 0 行），而"零岗位"从未被系统验证过。
这里的用例就是那条判据的可执行版本：断言状态码 + 空值结构，
而不是只看"没抛异常"。
"""
import pytest

from tests.helpers import admin_token, auth_headers

pytestmark = pytest.mark.asyncio

# 空库下应返回「空列表」的统计接口
EMPTY_LIST_PATHS = [
    "/api/jobs/stat/city",
    "/api/jobs/stat/company",
    "/api/jobs/stat/skill",
    "/api/jobs/stat/education",
    "/api/jobs/stat/experience",
    "/api/jobs/stat/status",
]

# 空库下必须仍是 7 个桶（计数全 0），不能因为没数据就少返回几个桶，
# 否则前端图表的 X 轴会凭空变短
SALARY_RANGE_LABELS = ["0-5k", "5k-10k", "10k-15k", "15k-20k", "20k-30k", "30k-50k", "50k+"]


@pytest.mark.parametrize("path", EMPTY_LIST_PATHS)
async def test_空库统计接口返回空列表(client, db_session, path):
    token = await admin_token(client, db_session)
    resp = await client.get(path, headers=auth_headers(token))
    assert resp.status_code == 200, f"{path} 在空库下必须返回 200，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] == []


async def test_空库薪资分布返回七个空桶(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.get("/api/jobs/stat/salary-range", headers=auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [item["name"] for item in data] == SALARY_RANGE_LABELS
    assert all(item["count"] == 0 for item in data)
    assert sum(item["count"] for item in data) == 0


async def test_空库汇总统计全为零(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.get("/api/jobs/analysis/summary", headers=auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 0
    assert data["active"] == 0
    # 空集上的平均值必须归零，不能是 None / NaN / 除零异常
    assert data["avg_salary"] == 0
    assert data["update_time"]


async def test_空库分页返回零总数与空列表(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/page", json={"page_num": 1, "page_size": 10}, headers=auth_headers(token)
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 0
    assert data["list"] == []


async def test_空库智能推荐返回空列表(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/recommend/intelligent",
        json={"skills": "Python,FastAPI", "city": "长沙", "experienceYears": 2, "limit": 5},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_空库技能画像零覆盖(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/skill-profile",
        json={"skills": "Python,FastAPI", "city": "长沙", "limit": 5},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["jobs"] == []
    assert data["summary"]["analyzed"] == 0
    # 分母为 0 时覆盖率必须是数字 0，而不是 NaN / 除零异常
    assert data["summary"]["covered_ratio"] == 0


async def test_空库导出返回合法xlsx(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/export", json={"page_num": 1, "page_size": 5}, headers=auth_headers(token)
    )
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    # 只有表头也必须是能打开的 xlsx（非空二进制），不能是 0 字节
    assert len(resp.content) > 0


async def test_空库兼容层AI分析不报错(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/ai-analysis", json={"keyword": "Python", "city": "长沙"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 结构必须完整：缺字段说明代码在空数据上走了另一条分支，前端会读不到
    for key in ("summary", "qualityJobs", "trendAnalysis", "skillDemands",
                "salaryAnalysis", "suggestions"):
        assert key in data, f"空库下 {key} 字段缺失"


async def test_空库日报生成成功且计数为零(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post("/api/reports/generate", headers=auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_jobs"] == 0
    assert data["active_jobs"] == 0
    assert data["new_jobs"] == 0
    assert data["ai_summary"], "空库日报也必须给出文字结论，不能是空串"
