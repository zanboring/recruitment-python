"""日报模块 FastAPI 集成测试。

覆盖 routes: /api/reports/*
- POST /generate    手动生成（管理员，幂等：同日重复返回同一记录）
- GET  /latest      最新日报（登录即可）
- GET  /list        列表
- GET  /{id}        详情（含统计快照）
- GET  /{id}/download  下载 Excel

设计要点：
- 用 admin_client 统一鉴权（与 test_model_router 同套路）；
- 生成链路中 AI 摘要已被 conftest 关闭模型，走规则兜底，离线可跑；
- generate 依赖真实统计，先插入岗位样本。
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from tests.helpers import auth_headers, admin_token

from app.services.job_service import JobService


async def _seed_jobs(client, token):
    """通过 /api/jobs/ 接口插入两笔岗位，保证统计非空且口径一致。"""
    for title, company, city in [
        ("Java开发工程师", "长沙某公司", "长沙"),
        ("Python工程师", "深圳某公司", "深圳"),
    ]:
        resp = await client.post("/api/jobs/", json={
            "title": title,
            "company_name": company,
            "source_site": "boss",
            "city": city,
            "experience": "1-3年",
            "education": "本科",
            "min_salary": 15000,
            "max_salary": 25000,
            "skills": "Python,Java,MySQL",
            "job_desc": title,
        }, headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
    return 2


@pytest_asyncio.fixture
async def admin_client(client, db_session):
    token = await admin_token(client, db_session)
    client.headers.update(auth_headers(token))
    return client


@pytest.mark.asyncio
async def test_generate_requires_admin(client, db_session):
    """未登录 / 非管理员访问 generate 应 401/403。"""
    resp = await client.post("/api/reports/generate")
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_generate_and_latest(admin_client):
    """生成日报后可查到最新一期，且统计非空、Excel 存在。"""
    n = await _seed_jobs(admin_client, admin_client.headers["Authorization"].split(" ")[1])

    resp = await admin_client.post("/api/reports/generate")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "GENERATED"
    assert data["new_jobs"] == n
    assert data["has_excel"] is True
    assert data["generated_by"] in ("primary", "local", "rule")

    latest = await admin_client.get("/api/reports/latest")
    assert latest.status_code == 200
    detail = latest.json()["data"]
    assert detail["stats"]["summary"]["active"] == n


@pytest.mark.asyncio
async def test_generate_idempotent(admin_client):
    """同一天重复生成应返回同一条记录。"""
    await _seed_jobs(admin_client, admin_client.headers["Authorization"].split(" ")[1])
    r1 = await admin_client.post("/api/reports/generate")
    r2 = await admin_client.post("/api/reports/generate")
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


@pytest.mark.asyncio
async def test_report_list_and_detail(admin_client):
    """列表 + 详情 + Excel 下载链路完整。"""
    await _seed_jobs(admin_client, admin_client.headers["Authorization"].split(" ")[1])
    await admin_client.post("/api/reports/generate")

    lst = await admin_client.get("/api/reports/list")
    assert lst.status_code == 200
    reports = lst.json()["data"]
    assert len(reports) >= 1
    rid = reports[0]["id"]

    detail = await admin_client.get(f"/api/reports/{rid}")
    assert detail.status_code == 200
    assert detail.json()["data"]["stats"]

    dl = await admin_client.get(f"/api/reports/{rid}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    ) or "octet-stream" in dl.headers["content-type"]
    assert dl.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_latest_empty_returns_404(admin_client):
    """还没有日报时 latest 应 404 并带提示。"""
    resp = await admin_client.get("/api/reports/latest")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_not_found(admin_client):
    resp = await admin_client.get("/api/reports/99999")
    assert resp.status_code == 404