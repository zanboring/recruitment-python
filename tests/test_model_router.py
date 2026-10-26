"""model 路由的 FastAPI TestClient 集成测试。

覆盖 routes: /api/model/*
- GET   /status    状态（primary/local/fallback/preference/version）
- GET   /list      模型清单
- POST  /switch    切换偏好（合法值/非法值/大小写归一）
- POST  /reload    重置偏好与健康缓存
- GET   /health    详细健康（带 timestamp）
- POST  /chat      智能路由对话（空消息、规则引擎兜底、Ollama 失败降级）

设计要点：
- mock 掉 httpx（Ollama /api/tags 健康探测）+ ai_service 的流式函数，
  保证测试离线可跑、不依赖真实 Ollama/智谱服务。
- 每个用例开始前显式重置 model_service 的进程内全局状态
  （_current_preference / _health_cache），避免用例间相互污染。
- conftest.py 的 autouse fixture 已重置限流与 ai_service 运行时状态。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tests.helpers import admin_token, auth_headers


@pytest.fixture(autouse=True)
def _reset_model_state():
    """每个用例前重置 model_service 的进程内全局状态。

    _current_preference 与 _health_cache 是模块级单例，被
    switch / reload / chat 修改后不重置会让用例间相互污染
    （比如上一个用例切到 primary，下一个用例期望 default=auto 就失败）。
    """
    from app.services import model_service

    model_service._current_preference = "auto"
    model_service._health_cache = {"data": None, "timestamp": 0}
    yield
    model_service._current_preference = "auto"
    model_service._health_cache = {"data": None, "timestamp": 0}


@pytest_asyncio.fixture
async def admin_client(client, db_session):
    """带管理员 Authorization 头的 client。

    /api/model/* 全系列接口都要求鉴权：status / health 需登录，
    switch / reload 需管理员。统一在这里登录一次并挂到 client 默认头上，
    省去每个用例重复取 token，也避免「接口加了鉴权但测试没带 token」
    导致整片用例莫名 401。
    """
    token = await admin_token(client, db_session)
    client.headers.update(auth_headers(token))
    return client


# ---------- GET /status ----------

@pytest.mark.asyncio
async def test_status_returns_three_models(admin_client):
    """GET /api/model/status 应返回 primary/local/fallback + preference/version。"""
    resp = await admin_client.get("/api/model/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert set(["primary", "local", "fallback"]).issubset(data.keys())
    assert data["fallback"]["available"] is True
    assert data["preference"] == "auto"
    assert data["version"] == "2.0.0"


# ---------- GET /list ----------

@pytest.mark.asyncio
async def test_list_returns_three_models(admin_client):
    """GET /api/model/list 应返回 list 长度=3，且三种 type 各出现一次。"""
    resp = await admin_client.get("/api/model/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    models = body["data"]
    assert isinstance(models, list)
    assert len(models) == 3
    types = [m["type"] for m in models]
    assert types.count("cloud") == 1
    assert types.count("local") == 1
    assert types.count("fallback") == 1


# ---------- POST /switch ----------

@pytest.mark.asyncio
async def test_switch_valid_returns_200(admin_client):
    """POST /api/model/switch 合法值（primary）→ code=0，preference=primary。"""
    resp = await admin_client.post("/api/model/switch", json={"model_name": "primary"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["preference"] == "primary"

    # 状态接口应同步反映新偏好
    status = await admin_client.get("/api/model/status")
    assert status.json()["data"]["preference"] == "primary"


@pytest.mark.asyncio
async def test_switch_invalid_returns_failed(admin_client):
    """POST /api/model/switch 非法值 → code=1（业务失败），HTTP 仍 200。

    这里用 Result.failed(message) 包装业务失败而非 HTTPException，
    因为「参数合法但业务不接受」不应当作请求格式错误。
    """
    resp = await admin_client.post("/api/model/switch", json={"model_name": "gpt-4"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] != 0
    assert "不支持" in body["message"]


@pytest.mark.asyncio
async def test_switch_case_insensitive(admin_client):
    """POST /api/model/switch 大小写无关：'PRIMARY' 也接受并归一为 'primary'。"""
    resp = await admin_client.post("/api/model/switch", json={"model_name": "PRIMARY"})
    assert resp.status_code == 200
    assert resp.json()["data"]["preference"] == "primary"


@pytest.mark.asyncio
async def test_switch_missing_model_name_returns_422(admin_client):
    """POST /api/model/switch 缺字段 → Pydantic 自动校验失败 → 422。"""
    resp = await admin_client.post("/api/model/switch", json={})
    assert resp.status_code == 422


# ---------- POST /reload ----------

@pytest.mark.asyncio
async def testreload_resets_preference_to_auto(admin_client):
    """POST /api/model/reload 应把偏好重置为 auto。"""
    # 先切到 local
    await admin_client.post("/api/model/switch", json={"model_name": "local"})
    # reload
    resp = await admin_client.post("/api/model/reload")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["preference"] == "auto"
    assert "primary" in body["data"]
    assert "local" in body["data"]


# ---------- GET /health ----------

@pytest.mark.asyncio
async def test_health_returns_timestamp(admin_client):
    """GET /api/model/health 应返回 primary/local/timestamp 字段。"""
    resp = await admin_client.get("/api/model/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "primary" in data
    assert "local" in data
    assert isinstance(data["timestamp"], int)
    assert data["timestamp"] > 0


# ---------- POST /chat ----------

@pytest.mark.asyncio
async def test_chat_missing_message_returns_422(admin_client):
    """POST /api/model/chat 缺 message 字段 → 422（Pydantic 校验）。"""
    resp = await admin_client.post("/api/model/chat", json={"use_local_model": False})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_empty_message_returns_failed(admin_client):
    """POST /api/model/chat 空消息 → code=1（业务失败），HTTP 仍 200。"""
    resp = await admin_client.post("/api/model/chat", json={"message": "   "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] != 0
    assert "消息" in body["message"]


@pytest.mark.asyncio
async def test_chat_falls_back_to_local_engine(admin_client):
    """无 GLM-4 key + Ollama 关闭 → 必须走规则引擎兜底，used_model=local_fallback。"""
    # 用 patch 兜底规则引擎的具体行为，避免依赖真实实现
    with patch(
        "app.services.local_model_service.LocalModelService.chat",
        new=AsyncMock(return_value="这是规则引擎的回答"),
    ):
        resp = await admin_client.post(
            "/api/model/chat", json={"message": "测试问题", "use_local_model": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["usedModel"] == "local_fallback"
        assert body["data"]["response"] == "这是规则引擎的回答"


@pytest.mark.asyncio
async def test_chat_ollama_failure_degrades_gracefully(admin_client):
    """Ollama 调用失败 → 必须降级到规则引擎，不应返回 500/抛异常。

    验证思路：启用 Ollama + GLM-4 key（让 _check_glm4 / _check_ollama_cached 都
    判定为 available），但 mock 掉 call_ollama_stream 让它抛异常。
    """
    from app.config import settings

    original_zhipu_key = settings.zhipuai_api_key
    original_ollama_enabled = settings.ollama_enabled
    settings.zhipuai_api_key = ""  # 关闭 GLM-4，确保走 Ollama → 失败 → 兜底
    settings.ollama_enabled = True

    try:
        # 让 Ollama 健康探测显示可用（不真发请求）
        async def fake_ollama_check():
            return {
                "available": True,
                "name": settings.ollama_model,
                "provider": "ollama",
                "type": "local",
            }

        # Ollama 调用抛异常（模拟服务挂了）
        async def fake_ollama_stream(message, session_id="", db=None, user_id=None):
            raise RuntimeError("Ollama 挂了")
            yield  # pragma: no cover  # 让它仍是 async generator

        with patch(
            "app.services.model_service._check_ollama_cached",
            new=fake_ollama_check,
        ), patch(
            "app.services.ai_service.call_ollama_stream",
            new=fake_ollama_stream,
        ), patch(
            "app.services.local_model_service.LocalModelService.chat",
            new=AsyncMock(return_value="兜底回答"),
        ):
            resp = await admin_client.post(
                "/api/model/chat", json={"message": "hi", "use_local_model": True}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == 0
            assert body["data"]["usedModel"] == "local_fallback"
            assert body["data"]["response"] == "兜底回答"
    finally:
        settings.zhipuai_api_key = original_zhipu_key
        settings.ollama_enabled = original_ollama_enabled


@pytest.mark.asyncio
async def test_chat_returns_full_response_envelope(admin_client):
    """POST /api/model/chat 成功时返回结构：{response, usedModel, success}。"""
    with patch(
        "app.services.local_model_service.LocalModelService.chat",
        new=AsyncMock(return_value="fallback content"),
    ):
        resp = await admin_client.post(
            "/api/model/chat", json={"message": "any"}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 三个字段都得有
        assert "response" in data
        assert "usedModel" in data
        assert "success" in data
        assert data["success"] is True