"""本地模型路由测试：按任务角色分配模型、不盲信配置、流式解析。

覆盖的核心行为：
1. 角色路由 —— 对话用语言类模型，结构化任务用代码类模型
2. 配置回退 —— 代码模型没配时自动降级到语言模型，只装一个模型也不用改配置
3. 安装校验 —— 配置了但没 pull 过的模型要显式报错，而不是静默 404 后被降级
4. 流式解析 —— Ollama 的 NDJSON 分片要能正确逐段产出
5. 偏好切换 —— 支持语义化偏好，也支持直接指定已安装的模型名
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ollama_client
from app.services.ollama_client import (
    ROLE_CHAT,
    ROLE_TOOL,
    LocalModelError,
    chat_candidates,
    resolve_model,
)


class _Cfg:
    """配置替身：避免为测试打补丁到全局 settings。"""

    def __init__(self, **overrides):
        self.ollama_model = "qwen2.5:14b"
        self.ollama_code_model = "qwen2.5-coder:7b"
        self.ollama_tool_model = ""
        self.ollama_base_url = "http://localhost:11434"
        self.ollama_enabled = True
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    ollama_client.reset_probe_cache()
    yield
    ollama_client.reset_probe_cache()


# ---------- 1. 角色路由 ----------

def test_对话角色使用语言类模型():
    assert resolve_model(ROLE_CHAT, _Cfg()) == "qwen2.5:14b"


def test_工具角色使用代码类模型():
    """结构化抽取交给小而快的专用模型。"""
    assert resolve_model(ROLE_TOOL, _Cfg()) == "qwen2.5-coder:7b"


def test_工具模型可被显式配置覆盖():
    cfg = _Cfg(ollama_tool_model="qwen2.5:14b")
    assert resolve_model(ROLE_TOOL, cfg) == "qwen2.5:14b"


def test_未配置代码模型时工具角色回退到语言模型():
    """只装了一个模型时不该因为缺配置而不可用。"""
    cfg = _Cfg(ollama_code_model="")
    assert resolve_model(ROLE_TOOL, cfg) == "qwen2.5:14b"


def test_未配置任何模型时给出兜底默认值():
    cfg = _Cfg(ollama_model="", ollama_code_model="")
    assert resolve_model(ROLE_CHAT, cfg) == "qwen2.5:14b"


def test_对话候选顺序为语言类优先且去重():
    """语言类优先保证对话质量；结构化模型作为兜底而非并列首选。"""
    assert chat_candidates(_Cfg()) == ["qwen2.5:14b", "qwen2.5-coder:7b"]


def test_两个角色指向同一模型时不重复():
    cfg = _Cfg(ollama_code_model="qwen2.5:14b")
    assert chat_candidates(cfg) == ["qwen2.5:14b"]


# ---------- 2. 安装校验：不盲信配置 ----------

@pytest.mark.asyncio
async def test_模型未安装时抛出带清单的错误():
    """配置了没 pull 过的模型，必须在调用前就报错。"""
    with patch.object(ollama_client, "probe", AsyncMock(return_value=(["llama3:8b"], ""))):
        with pytest.raises(LocalModelError) as exc:
            await ollama_client.ensure_model("qwen2.5:14b", _Cfg())

    message = str(exc.value)
    assert "qwen2.5:14b" in message
    assert "llama3:8b" in message          # 把现有模型列出来，便于直接改正
    assert "ollama pull" in message        # 给出可执行的修复动作


@pytest.mark.asyncio
async def test_模型已安装时校验通过():
    with patch.object(ollama_client, "probe", AsyncMock(return_value=(["qwen2.5:14b"], ""))):
        await ollama_client.ensure_model("qwen2.5:14b", _Cfg())  # 不抛异常即通过


@pytest.mark.asyncio
async def test_探测失败时不阻拦调用():
    """拿不到清单（网络抖动）不应让本地推理整体不可用。"""
    with patch.object(ollama_client, "probe", AsyncMock(return_value=([], "ConnectError"))):
        await ollama_client.ensure_model("qwen2.5:14b", _Cfg())


@pytest.mark.asyncio
async def test_带latest后缀的模型名可匹配():
    with patch.object(ollama_client, "probe", AsyncMock(return_value=(["qwen2.5:14b:latest"], ""))):
        await ollama_client.ensure_model("qwen2.5:14b", _Cfg())


@pytest.mark.asyncio
async def test_探测失败不写入缓存():
    """网络抖动不能被放大成 30 秒的假故障。"""
    failing = AsyncMock(side_effect=ConnectionError("boom"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = failing

    with patch("app.services.ollama_client.httpx.AsyncClient", return_value=mock_client):
        models, error = await ollama_client.probe(cfg=_Cfg())
        assert models == []
        assert error == "ConnectionError"

    # 缓存仍为空 → 下一次会重新探测（而不是直接命中失败的缓存）
    assert ollama_client._probe_cache == {}


# ---------- 3. 流式解析 ----------

class _FakeStream:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return b"".join(line.encode() for line in self._lines)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _client_with_stream(lines, status_code=200):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.stream = MagicMock(return_value=_FakeStream(lines, status_code))
    return client


@pytest.mark.asyncio
async def test_流式响应按分片产出文本():
    lines = [
        '{"message": {"content": "你好"}, "done": false}',
        "",  # 空行应被跳过
        '{"message": {"content": "，世界"}, "done": false}',
        '{"message": {"content": ""}, "done": true}',
    ]
    client = _client_with_stream(lines)
    with patch.object(ollama_client, "ensure_model", AsyncMock()), \
         patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        chunks = [c async for c in ollama_client.chat_stream([{"role": "user", "content": "hi"}], cfg=_Cfg())]

    assert chunks == ["你好", "，世界"]


@pytest.mark.asyncio
async def test_流式响应遇到非法JSON分片时跳过而不中断():
    lines = [
        "not-json",
        '{"message": {"content": "OK"}, "done": true}',
    ]
    client = _client_with_stream(lines)
    with patch.object(ollama_client, "ensure_model", AsyncMock()), \
         patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        chunks = [c async for c in ollama_client.chat_stream([{"role": "user", "content": "hi"}], cfg=_Cfg())]

    assert chunks == ["OK"]


@pytest.mark.asyncio
async def test_非200响应抛出可读错误():
    client = _client_with_stream(['{"error": "model not found"}'], status_code=404)
    with patch.object(ollama_client, "ensure_model", AsyncMock()), \
         patch("app.services.ollama_client.httpx.AsyncClient", return_value=client):
        with pytest.raises(LocalModelError) as exc:
            [c async for c in ollama_client.chat_stream([{"role": "user", "content": "hi"}], cfg=_Cfg())]

    assert "404" in str(exc.value)


# ---------- 4. 本地工具识别 ----------

@pytest.mark.asyncio
async def test_本地工具识别复用云端解析逻辑():
    """本地路径与云端走同一套提示词与解析，保证降级后行为一致。"""
    reply = {"content": '{"tool": "query_jobs", "args": {"city": "长沙"}}'}
    with patch.object(ollama_client, "chat_sync", AsyncMock(return_value=reply)) as mocked:
        result = await ollama_client.detect_tool_call("长沙有什么岗位", _Cfg())

    assert result == {"tool": "query_jobs", "args": {"city": "长沙"}}
    # 必须使用「结构化」角色，而不是对话模型
    assert mocked.call_args.kwargs["role"] == ROLE_TOOL


@pytest.mark.asyncio
async def test_本地工具识别对闲聊返回None():
    with patch.object(ollama_client, "chat_sync", AsyncMock(return_value={"content": "NONE"})):
        assert await ollama_client.detect_tool_call("你好", _Cfg()) is None


@pytest.mark.asyncio
async def test_本地工具识别走结构化模型而不是对话模型():
    """这是「用对模型」的关键：实测两者准确率相同，但结构化模型快一倍。"""
    captured = {}

    async def fake_chat_sync(messages, model=None, role=ROLE_CHAT, **kwargs):
        captured["model"] = model or resolve_model(role, kwargs.get("cfg"))
        return {"content": "NONE"}

    with patch.object(ollama_client, "chat_sync", fake_chat_sync):
        await ollama_client.detect_tool_call("你好", _Cfg())

    assert captured["model"] == "qwen2.5-coder:7b"


# ---------- 5. 偏好切换 ----------

@pytest.mark.asyncio
async def test_切换到语义化偏好local_code():
    from app.services import model_service

    original = model_service._current_preference
    try:
        result = await model_service.ModelService.switch("local_code")
        assert result["success"] is True
        assert model_service._current_preference == "local_code"
    finally:
        model_service._current_preference = original


@pytest.mark.asyncio
async def test_可直接指定已安装的模型名():
    from app.services import model_service

    original = model_service._current_preference
    try:
        with patch.object(ollama_client, "probe", AsyncMock(return_value=(["qwen2.5-coder:7b"], ""))):
            result = await model_service.ModelService.switch("qwen2.5-coder:7b")
        assert result["success"] is True
        assert model_service._current_preference == "qwen2.5-coder:7b"
    finally:
        model_service._current_preference = original


@pytest.mark.asyncio
async def test_拒绝切换到未安装的模型():
    """切到一个没 pull 过的模型会表现为「切完就不好用」，必须拦住。"""
    from app.services import model_service

    original = model_service._current_preference
    try:
        with patch.object(ollama_client, "probe", AsyncMock(return_value=(["qwen2.5:14b"], ""))):
            result = await model_service.ModelService.switch("不存在的模型")
        assert result["success"] is False
        assert model_service._current_preference == original
        assert "qwen2.5:14b" in result["message"]  # 提示现有可用模型
    finally:
        model_service._current_preference = original
