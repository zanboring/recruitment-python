"""视觉导入路由的参数绑定契约（回归）。

背景：``/api/jobs/vision-import`` 与 ``vision-batch-import`` 曾把 ``validate``
声明成 JSON body（``request: VisionImportRequest = Body(default=None)``），却与
multipart 文件上传混用。multipart 请求没有 JSON body，``request`` 恒为 None，
于是**入库分支永远走不到** —— 前端点「确认入库」只会一直返回 ``saved: false``。
前端调用的其实是 ``?validate=true``（``DataManageHeader.vue:265``），契约从一开始就是断的。

接口层此前没有任何用例（``grep vision-import tests/`` 为空），所以缺陷长期未暴露。
本文件钉住两件事：
1. ``validate`` 必须是 **query 参数**，requestBody 只能是 multipart；
2. 带 ``?validate=true`` 时确实走入库分支，且 job_key 幂等去重生效。
"""

import base64
import types

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

# 1x1 透明 PNG，够通过上传校验即可（识别函数在用例里被替换）
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FAKE_JOB = {
    "title": "Python 后端工程师",
    "company_name": "测试科技有限公司",
    "city": "长沙",
    "experience": "1-3年",
    "education": "本科",
    "min_salary": 12000,
    "max_salary": 18000,
    "skills": "Python, FastAPI",
    "job_desc": "负责后端服务开发",
}


@pytest_asyncio.fixture
async def admin_client(app, monkeypatch):
    """带管理员身份、且视觉识别被替换成桩的客户端。"""
    from app.dependencies import get_current_user
    from app.services import vision_service

    # require_admin 判的是 user.role != "ADMIN"（大写），必须对齐
    fake_user = types.SimpleNamespace(id=1, username="test-admin", role="ADMIN")

    async def _fake_one(image_bytes, mime="image/jpeg"):
        return dict(FAKE_JOB)

    async def _fake_batch(images):
        return [
            {
                "index": item["index"],
                "ok": item["error"] is None,
                "result": dict(FAKE_JOB) if item["error"] is None else None,
                "error": item["error"],
            }
            for item in images
        ]

    monkeypatch.setattr(vision_service, "recognize_job_image", _fake_one)
    monkeypatch.setattr(vision_service, "recognize_job_images_batch", _fake_batch)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://pytest") as c:
        yield c


async def test_validate_is_query_param_not_json_body(app, admin_client):
    """契约：validate 在 query 上；requestBody 只能是 multipart。"""
    spec = app.openapi()
    op = spec["paths"]["/api/jobs/vision-import"]["post"]

    params = [(p["name"], p["in"]) for p in op.get("parameters", [])]
    assert ("validate", "query") in params, f"validate 应是 query 参数，实际 {params}"

    media = list((op.get("requestBody") or {}).get("content", {}).keys())
    assert media == ["multipart/form-data"], f"requestBody 应只有 multipart，实际 {media}"
    assert "application/json" not in media, (
        "requestBody 里出现 application/json —— multipart 请求不会有 JSON body，"
        "validate 会恒为默认值，入库分支永远走不到"
    )


async def test_preview_does_not_persist(admin_client):
    """不带 validate：只返回识别结果，不写库。"""
    r = await admin_client.post(
        "/api/jobs/vision-import", files={"file": ("a.png", PNG, "image/png")}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["saved"] is False
    assert data["result"]["title"] == FAKE_JOB["title"]


async def test_validate_true_persists_and_is_idempotent(admin_client):
    """带 validate=true：入库；同一张再提交按 job_key 幂等，不会重复。"""
    files = {"file": ("a.png", PNG, "image/png")}

    first = await admin_client.post("/api/jobs/vision-import?validate=true", files=files)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["saved"] is True
    assert first.json()["data"]["job_id"]

    again = await admin_client.post("/api/jobs/vision-import?validate=true", files=files)
    assert again.status_code == 400
    assert "已存在" in again.json()["message"]


async def test_batch_validate_true_persists(admin_client):
    """批量导入同样受 validate 控制，且成功条能入库。"""
    preview = await admin_client.post(
        "/api/jobs/vision-batch-import",
        files=[("files", ("a.png", PNG, "image/png"))],
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["saved"] is False

    saved = await admin_client.post(
        "/api/jobs/vision-batch-import?validate=true",
        files=[("files", ("a.png", PNG, "image/png"))],
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()["data"]
    assert body["saved"] is True
    assert body["results"][0]["saved"] is True
