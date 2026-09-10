"""model_service 的单元测试。

覆盖：
- get_status / get_list：返回三级模型（primary/local/fallback）+ 偏好字段
- switch：合法/非法输入
- reload：清缓存 + 重新探测
- health：返回 primary/local 详情 + timestamp
- chat：空消息、use_local_model 走 ollama、规则引擎兜底（无 ollama 无 glm4）

策略：patch httpx.AsyncClient 隔离外部依赖；不依赖真实 Ollama / 智谱服务。
    pytest tests/test_model_service.py -v
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 在导入 app.* 前 patch 掉 settings，避免读真实环境变量
@pytest.fixture(autouse=True)
def _patch_settings():
    """让 settings 中的关键字段可控：关闭 ollama、清空云端密钥。

    云端供应商列表由 `llm_client.get_cloud_providers(settings)` 生成，
    而 model_service 会把**自己的 settings 引用**传进去，
    因此 patch 这里就能完整隔离云端配置（无需再 patch llm_client）。
    """
    with patch("app.services.model_service.settings") as mock_settings:
        # 默认：没有任何云端密钥、关闭 Ollama
        mock_settings.deepseek_api_key = ""
        mock_settings.deepseek_api_url = "https://api.deepseek.com/chat/completions"
        mock_settings.deepseek_model = "deepseek-flash"
        mock_settings.deepseek_reasoning_effort = "none"
        mock_settings.zhipuai_api_key = ""
        mock_settings.zhipuai_api_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        mock_settings.zhipuai_model = "glm-4-flash"
        mock_settings.ai_provider = "deepseek"
        mock_settings.ollama_enabled = False
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.ollama_model = "qwen2.5:14b"
        mock_settings.ollama_code_model = "qwen2.5-coder:7b"
        mock_settings.ollama_tool_model = ""
        yield mock_settings


@pytest.fixture
def ollama_available_settings(_patch_settings):
    """启用 Ollama + GLM-4 key 场景。"""
    _patch_settings.ollama_enabled = True
    _patch_settings.zhipuai_api_key = "fake-key"
    return _patch_settings


@pytest.fixture
def glm4_only_settings(_patch_settings):
    """仅 GLM-4 可用（Ollama 关闭）场景。"""
    _patch_settings.zhipuai_api_key = "fake-key"
    return _patch_settings


# ---------- 静态探测 ----------

def test_未配置任何云端密钥时标记不可用(_patch_settings):
    """没有任何云端密钥时，云端模型应标记为不可用。"""
    from app.services.model_service import _check_cloud_llm

    r = _check_cloud_llm()
    assert r["type"] == "cloud"
    assert r["available"] is False
    assert "未配置" in r["description"]


def test_配置DeepSeek密钥后作为主服务商(_patch_settings):
    """DeepSeek 是默认主服务商，配好密钥后状态应展示它。"""
    _patch_settings.deepseek_api_key = "test-key"
    from app.services.model_service import _check_cloud_llm

    r = _check_cloud_llm()
    assert r["available"] is True
    assert r["provider"] == "deepseek"
    assert r["name"] == "DeepSeek"
    assert "API Key" in r["description"]


def test_主服务商缺密钥时备选仍可用(_patch_settings):
    """多供应商容灾：主服务商未配置，但备选配了 —— 云端整体仍可用。

    这条用例守住的是「状态展示不能误导」：只要还有可用的云端供应商，
    就不该显示「云端不可用」，否则运维会误判对话链路已断。
    """
    _patch_settings.deepseek_api_key = ""
    _patch_settings.zhipuai_api_key = "backup-key"
    from app.services.model_service import _check_cloud_llm

    r = _check_cloud_llm()
    assert r["available"] is True
    assert r["provider"] == "zhipu"


def test_两个云端都配置时DeepSeek优先并标注备选(_patch_settings):
    _patch_settings.deepseek_api_key = "main-key"
    _patch_settings.zhipuai_api_key = "backup-key"
    from app.services.model_service import _check_cloud_llm

    r = _check_cloud_llm()
    assert r["provider"] == "deepseek"
    assert "备选" in r["description"]


def test_AI_PROVIDER设为zhipu时智谱优先(_patch_settings):
    """优先级可通过配置显式调整，不必改代码。"""
    _patch_settings.deepseek_api_key = "main-key"
    _patch_settings.zhipuai_api_key = "backup-key"
    _patch_settings.ai_provider = "zhipu"
    from app.services.model_service import _check_cloud_llm

    r = _check_cloud_llm()
    assert r["provider"] == "zhipu"


@pytest.mark.asyncio
async def test_check_ollama_disabled(_patch_settings):
    """Ollama 关闭时直接返回不可用（不发请求）。"""
    from app.services.model_service import _check_ollama
    r = await _check_ollama()
    assert r["available"] is False
    assert "未启用" in r["description"]


def _mock_tags_client(models, exc=None):
    """构造一个返回指定模型清单的 httpx.AsyncClient 替身。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(return_value=None)
    mock_resp.json = MagicMock(return_value={"models": [{"name": m} for m in models]})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.mark.asyncio
async def test_check_ollama_reachable(ollama_available_settings):
    """Ollama 可达且配置的模型都已安装 → available=true。"""
    from app.services import ollama_client

    ollama_client.reset_probe_cache()
    client = _mock_tags_client(["qwen2.5:14b", "qwen2.5-coder:7b"])
    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        from app.services.model_service import _check_ollama

        r = await _check_ollama()
        assert r["available"] is True
        assert r["chat_model"] == "qwen2.5:14b"
        assert r["tool_model"] == "qwen2.5-coder:7b"
        assert r["missing"] == []
    ollama_client.reset_probe_cache()


@pytest.mark.asyncio
async def test_check_ollama_unreachable(ollama_available_settings):
    """httpx 抛异常 → available=false，描述含异常类型。"""
    from app.services import ollama_client

    ollama_client.reset_probe_cache()
    client = _mock_tags_client([], exc=ConnectionError("refused"))
    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        from app.services.model_service import _check_ollama

        r = await _check_ollama()
        assert r["available"] is False
        assert "ConnectionError" in r["description"]
    ollama_client.reset_probe_cache()


@pytest.mark.asyncio
async def test_服务可达但模型未安装时如实报告(ollama_available_settings):
    """回归：只看 /api/tags 的 200 会谎报可用，必须核对模型是否真的装过。

    这是本项目真实踩过的坑 —— 配置写的是 qwen2:7b，而本机装的是 qwen2.5:14b，
    健康检查却报告「已就绪」，直到调用时才 404 并被静默降级到规则引擎。
    """
    from app.services import ollama_client

    ollama_client.reset_probe_cache()
    client = _mock_tags_client(["llama3:8b"])  # 服务在跑，但配置的模型一个都没有
    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        from app.services.model_service import _check_ollama

        r = await _check_ollama()
        assert r["available"] is False
        assert "未安装" in r["description"]
        assert set(r["missing"]) == {"qwen2.5:14b", "qwen2.5-coder:7b"}
        assert r["installed"] == ["llama3:8b"]
    ollama_client.reset_probe_cache()


@pytest.mark.asyncio
async def test_对话模型缺失但结构化模型可用时仍如实标注(ollama_available_settings):
    """对话模型没装 → 本地这一级判定为不可用（降级链会跳过它）。"""
    from app.services import ollama_client

    ollama_client.reset_probe_cache()
    client = _mock_tags_client(["qwen2.5-coder:7b"])
    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        from app.services.model_service import _check_ollama

        r = await _check_ollama()
        assert r["available"] is False
        assert r["missing"] == ["qwen2.5:14b"]
    ollama_client.reset_probe_cache()


@pytest.mark.asyncio
async def test_角色明细标注每个模型是否已安装(ollama_available_settings):
    """local.models 应逐角色说明配置与安装情况，便于状态页定位缺哪个模型。"""
    from app.services import ollama_client

    ollama_client.reset_probe_cache()
    client = _mock_tags_client(["qwen2.5:14b"])
    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        from app.services.model_service import _check_ollama

        r = await _check_ollama()
        detail = {item["role"]: item for item in r["models"]}
        assert detail["chat"]["model"] == "qwen2.5:14b"
        assert detail["chat"]["installed"] is True
        assert detail["tool"]["model"] == "qwen2.5-coder:7b"
        assert detail["tool"]["installed"] is False
    ollama_client.reset_probe_cache()


# ---------- ModelService 接口 ----------

@pytest.mark.asyncio
async def test_get_status_returns_three_models(_patch_settings):
    """get_status 应包含 primary / local / fallback + preference + version。"""
    from app.services.model_service import ModelService

    status = await ModelService.get_status()
    assert set(["primary", "local", "fallback"]).issubset(status.keys())
    assert status["fallback"]["available"] is True  # 规则引擎始终可用
    assert status["preference"] == "auto"  # 默认偏好
    assert status["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_get_list_returns_three_models(_patch_settings):
    """get_list 应返回 list 长度=3。"""
    from app.services.model_service import ModelService

    models = await ModelService.get_list()
    assert isinstance(models, list)
    assert len(models) == 3
    types = [m["type"] for m in models]
    assert types.count("cloud") == 1
    assert types.count("local") == 1
    assert types.count("fallback") == 1


@pytest.mark.asyncio
async def test_switch_valid(_patch_settings):
    """switch 接受 primary/local/auto。"""
    from app.services.model_service import ModelService

    for name in ("primary", "local", "auto"):
        r = await ModelService.switch(name)
        assert r["success"] is True
        assert r["preference"] == name


@pytest.mark.asyncio
async def test_switch_invalid(_patch_settings):
    """switch 非法值 → success=False。"""
    from app.services.model_service import ModelService

    r = await ModelService.switch("gpt-4")
    assert r["success"] is False
    assert "不支持" in r["message"]


@pytest.mark.asyncio
async def test_switch_normalizes_case(_patch_settings):
    """switch 大小写无关（'PRIMARY' 也接受）。"""
    from app.services.model_service import ModelService

    r = await ModelService.switch("PRIMARY")
    assert r["success"] is True
    assert r["preference"] == "primary"


@pytest.mark.asyncio
async def test_reload_resets_preference(_patch_settings):
    """reload 应把偏好重置为 auto。"""
    from app.services import model_service
    from app.services.model_service import ModelService

    # 先切到 primary，再 reload
    model_service._current_preference = "primary"
    r = await ModelService.reload()
    assert r["success"] is True
    assert r["preference"] == "auto"
    # 健康缓存已重置（data 字段被设为 None 或被新探测值覆盖）
    # 这里仅验证 reload 路径无异常，缓存语义在集成场景验证


@pytest.mark.asyncio
async def test_health_returns_timestamp(_patch_settings):
    """health 应返回 primary/local/timestamp。"""
    from app.services.model_service import ModelService

    before = int(time.time())
    h = await ModelService.health()
    after = int(time.time())
    assert before <= h["timestamp"] <= after
    assert "primary" in h and "local" in h


# ---------- chat 路由 ----------

@pytest.mark.asyncio
async def test_chat_empty_message(_patch_settings):
    """chat 空消息 → success=False。"""
    from app.services.model_service import ModelService

    r = await ModelService.chat("   ", use_local_model=False, db=None)
    assert r["success"] is False
    assert "消息内容" in r["error"]


@pytest.mark.asyncio
async def test_chat_falls_back_to_local_engine(glm4_only_settings):
    """Ollama 关闭 + 无 GLM-4 key → 必须走规则引擎兜底（永远可用）。"""
    from app.services.model_service import ModelService

    # GLM-4 key = "fake-key" 但我们不真发请求，因为 glm4.available 是基于配置判断
    # 我们这里把 key 也清空，强制触发兜底
    glm4_only_settings.zhipuai_api_key = ""

    with patch("app.services.local_model_service.LocalModelService.chat",
               new=AsyncMock(return_value="这是规则引擎的回答")):
        r = await ModelService.chat("测试问题", use_local_model=False, db=None)
        assert r["success"] is True
        assert r["used_model"] == "local_fallback"
        assert r["response"] == "这是规则引擎的回答"


@pytest.mark.asyncio
async def test_chat_use_local_prefers_ollama(ollama_available_settings):
    """use_local_model=True 且 Ollama 可达 → 优先走 Ollama。"""
    from app.services.model_service import ModelService

    # **kwargs 用于吞掉后续新增的 model 参数（调用签名会随模型路由扩展）
    async def fake_ollama_stream(message, prompt, db, user_id=None, **kwargs):
        yield "Ollama"
        yield "回复"

    # mock 掉健康探测避免真实 HTTP 请求
    async def fake_ollama_check():
        return {"available": True, "name": "qwen2.5:14b", "provider": "ollama",
                "type": "local", "chat_model": "qwen2.5:14b"}

    def fake_providers(cfg=None):
        return []  # 模拟「未配置任何云端密钥」，强制走本地/兜底链路

    with patch("app.services.model_service._check_ollama_cached",
               new=fake_ollama_check), \
         patch("app.services.model_service.get_cloud_providers",
               new=fake_providers), \
         patch("app.services.ai_service.call_ollama_stream",
               new=fake_ollama_stream):
        r = await ModelService.chat("hello", use_local_model=True, db=None)
        assert r["success"] is True
        assert r["used_model"] == "local"
        assert r["response"] == "Ollama回复"


@pytest.mark.asyncio
async def test_chat_preference_local(ollama_available_settings):
    """preference=local + Ollama 可用 → used_model=local。"""
    from app.services import model_service
    from app.services.model_service import ModelService

    model_service._current_preference = "local"

    async def fake_ollama_stream(message, prompt, db, user_id=None, **kwargs):
        yield "OK"

    # mock 掉健康探测，避免真 HTTP 请求失败导致降级
    async def fake_ollama_check():
        return {"available": True, "name": "qwen2.5:14b", "provider": "ollama",
                "type": "local", "chat_model": "qwen2.5:14b"}

    # 模拟「未配置任何云端密钥」，强制走本地链路
    def fake_providers(cfg=None):
        return []

    with patch("app.services.model_service._check_ollama_cached",
               new=fake_ollama_check), \
         patch("app.services.model_service.get_cloud_providers",
               new=fake_providers), \
         patch("app.services.ai_service.call_ollama_stream",
               new=fake_ollama_stream):
        r = await ModelService.chat("hi", use_local_model=False, db=None)
        assert r["used_model"] == "local"


@pytest.mark.asyncio
async def test_chat_ollama_failure_falls_back_to_local_engine(ollama_available_settings):
    """Ollama 调用失败 → 必须降级到规则引擎（不抛异常）。"""
    from app.services.model_service import ModelService

    # 强制 GLM-4 也「不可用」以确保兜底
    ollama_available_settings.zhipuai_api_key = ""

    async def fake_ollama_fail(message, prompt, db, **kwargs):
        raise RuntimeError("Ollama 挂了")
        yield  # 让它变成生成器  # pragma: no cover

    with patch("app.services.ai_service.call_ollama_stream",
               new=fake_ollama_fail), \
         patch("app.services.local_model_service.LocalModelService.chat",
               new=AsyncMock(return_value="fallback")):
        r = await ModelService.chat("hi", use_local_model=True, db=None)
        assert r["success"] is True
        assert r["used_model"] == "local_fallback"
        assert r["response"] == "fallback"