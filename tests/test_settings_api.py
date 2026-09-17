# -*- coding: utf-8 -*-
"""设置中心 API 测试：GET 脱敏读取 / POST 保存（白名单 + camelCase + 换行拒绝）。"""
import pytest

from app.config import RUNTIME_EDITABLE_KEYS


async def _admin_client(client, db_session):
    """复用 test_model_router 的鉴权模式：登录 admin 并挂 Authorization 头。"""
    from tests.test_model_router import admin_token, auth_headers

    token = await admin_token(client, db_session)
    client.headers.update(auth_headers(token))
    return client


@pytest.mark.asyncio
async def test_get_provider_masked(client, db_session):
    """GET /api/settings/provider：返回脱敏 key + 白名单可编辑键列表。"""
    c = await _admin_client(client, db_session)
    resp = await c.get("/api/settings/provider")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    # 脱敏展示：不得出现完整 key（真实环境可能已配置，断言格式而非值）
    for field in ("deepseekApiKey", "zhipuaiApiKey"):
        val = data.get(field) or ""
        if "sk-" in val:
            assert "****" in val and len(val) <= 20
    assert set(RUNTIME_EDITABLE_KEYS).issubset(data["editableKeys"])


@pytest.mark.asyncio
async def test_post_provider_requires_admin(client):
    """POST 保存需要管理员；匿名请求应 401。"""
    resp = await client.post("/api/settings/provider", json={"deepseekModel": "deepseek-chat"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_post_provider_camelcase_and_whitelist(client, db_session, tmp_path):
    """POST camelCase 字段被接受；越权键（jwt_secret）被忽略；真实写入白名单键。"""
    import app.config as cfg

    fake_env = tmp_path / "config.env"
    fake_env.write_text("# 模板注释\nDEEPSEEK_API_KEY=\n", encoding="utf-8")

    def fake_runtime_env_file():
        return str(fake_env)

    cfg.save_runtime_config.__globals__["runtime_env_file"] = fake_runtime_env_file

    c = await _admin_client(client, db_session)
    resp = await c.post("/api/settings/provider", json={
        "deepseekApiKey": "sk-test-camel-123",
        "deepseekModel": "deepseek-chat",
        "jwtSecret": "should-not-write",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    written = body["data"]["written"]
    assert "deepseek_api_key" in written
    assert "deepseek_model" in written
    assert "jwt_secret" not in written  # 越权键被白名单挡掉

    disk = fake_env.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-test-camel-123" in disk
    assert "DEEPSEEK_MODEL=deepseek-chat" in disk
    assert "# 模板注释" in disk  # 注释保留（缺陷 1 修复）
    assert "JWT_SECRET" not in disk  # 未注入


@pytest.mark.asyncio
async def test_post_provider_rejects_newline(client, db_session, tmp_path):
    """值含换行应被拒绝（配置注入防护，缺陷 3 修复）。"""
    import app.config as cfg

    fake_env = tmp_path / "config.env"
    fake_env.write_text("", encoding="utf-8")

    def fake_runtime_env_file():
        return str(fake_env)

    cfg.save_runtime_config.__globals__["runtime_env_file"] = fake_runtime_env_file

    c = await _admin_client(client, db_session)
    resp = await c.post("/api/settings/provider", json={
        "deepseekApiKey": "sk-abc\nJWT_SECRET=hacked",
    })
    # 核心断言：注入行没有写盘
    disk = fake_env.read_text(encoding="utf-8")
    assert "JWT_SECRET=hacked" not in disk
    # 补充断言（回应 WorkBuddy 500 关切）：换行错误应返回业务错误而非 HTTP 500
    assert resp.status_code != 500
    body = resp.json()
    assert body.get("code") != 0  # Result.failed
    assert "换行" in (body.get("message") or "")


@pytest.mark.asyncio
async def test_post_provider_empty_body_fails(client, db_session):
    """空 body 应报「没有需要保存的配置」。"""
    c = await _admin_client(client, db_session)
    resp = await c.post("/api/settings/provider", json={})
    body = resp.json()
    assert body["code"] != 0
