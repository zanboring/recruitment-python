"""输出长度上限测试。

背景：原实现**不给任何请求设置输出长度上限** ——
- 云端请求体里没有 ``max_tokens``，用服务端默认值；
- 本地对话不传 ``num_predict``，而 Ollama 的默认值是 ``-1``（不限制）。

后果是单次回答的长度没有任何上界：按 token 计费的云端模型成本不可控，
本地模型（实测 12 tok/s）一次不设上限的回答足以让用户等上几分钟。
本文件确保两条链路都带上了上限，且「工具识别」这类短输出场景被单独收紧。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.llm_client import CloudProvider, complete_chat, stream_chat


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """同时承担流式与非流式两种调用，记录最后一次请求体。"""

    recorded: dict = {}
    response_json: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None):
        _FakeClient.recorded = {"payload": json}
        return _FakeStreamResponse(['data: [DONE]'])

    async def post(self, url, headers=None, json=None):
        _FakeClient.recorded = {"payload": json}
        resp = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: _FakeClient.response_json,
        )
        return resp


def _provider() -> CloudProvider:
    return CloudProvider(
        name="DeepSeek", provider="deepseek",
        api_url="https://api.deepseek.com/chat/completions",
        api_key="sk-test", model="deepseek-flash",
    )


# ---------------------------------------------------------------- 云端链路

@pytest.mark.asyncio
async def test_流式请求带上输出上限():
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeClient):
        async for _ in stream_chat(
            _provider(), [{"role": "user", "content": "hi"}], max_tokens=321
        ):
            pass

    assert _FakeClient.recorded["payload"]["max_tokens"] == 321


@pytest.mark.asyncio
async def test_非流式请求带上输出上限():
    _FakeClient.response_json = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeClient):
        await complete_chat(
            _provider(), [{"role": "user", "content": "hi"}], max_tokens=64
        )

    assert _FakeClient.recorded["payload"]["max_tokens"] == 64


@pytest.mark.asyncio
async def test_不传上限时不产生该字段():
    """留空表示交给服务端默认值，不应写入空值把请求弄脏。"""
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeClient):
        async for _ in stream_chat(_provider(), [{"role": "user", "content": "hi"}]):
            pass

    assert "max_tokens" not in _FakeClient.recorded["payload"]


# ---------------------------------------------------------------- 本地链路

class _FakeOllamaStream:
    def __init__(self):
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b""

    async def aiter_lines(self):
        yield '{"message": {"content": "x"}, "done": true}'


class _FakeOllamaClient:
    recorded: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None):
        _FakeOllamaClient.recorded = {"payload": json}
        return _FakeOllamaStream()


@pytest.mark.asyncio
async def test_本地对话把上限映射为num_predict():
    """Ollama 默认 num_predict=-1（不限），必须显式覆盖。"""
    from app.services import ollama_client

    with patch.object(ollama_client, "ensure_model", AsyncMock()), \
         patch("app.services.ollama_client.httpx.AsyncClient", _FakeOllamaClient):
        chunks = [
            c async for c in ollama_client.chat_stream(
                [{"role": "user", "content": "hi"}],
                role=ollama_client.ROLE_CHAT, max_tokens=256,
            )
        ]

    assert chunks == ["x"]
    assert _FakeOllamaClient.recorded["payload"]["options"]["num_predict"] == 256


@pytest.mark.asyncio
async def test_本地对话保留调用方传入的其他options():
    """合并而非覆盖：调用方给的 temperature 等参数不能被上限挤掉。"""
    from app.services import ollama_client

    with patch.object(ollama_client, "ensure_model", AsyncMock()), \
         patch("app.services.ollama_client.httpx.AsyncClient", _FakeOllamaClient):
        [c async for c in ollama_client.chat_stream(
            [{"role": "user", "content": "hi"}],
            options={"temperature": 0.1}, max_tokens=64,
        )]

    opts = _FakeOllamaClient.recorded["payload"]["options"]
    assert opts["temperature"] == 0.1
    assert opts["num_predict"] == 64


# ---------------------------------------------------------------- 配置与接线

def test_默认上限已配置且逐级放宽():
    assert settings.ai_max_output_tokens > 0
    # 工具识别只需一行 JSON，应比对话上限更紧
    assert settings.ai_tool_detect_max_tokens < settings.ai_max_output_tokens
    # 分析报告需要长输出，应比对话上限更宽
    assert settings.ai_analysis_max_output_tokens > settings.ai_max_output_tokens


@pytest.mark.asyncio
async def test_对话入口使用配置的上限():
    """接线检查：call_cloud_stream / call_ollama_stream 必须真的把上限传下去。"""
    from app.services import ai_service

    seen = {}

    async def fake_stream_chat(provider, messages, temperature=None, on_usage=None,
                               timeout=None, max_tokens=None):
        seen["cloud"] = max_tokens
        yield "x"

    async def fake_local_chat_stream(messages, model=None, role=None,
                                     on_usage=None, max_tokens=None, **kwargs):
        seen["local"] = max_tokens
        yield "x"

    provider = _provider()
    with patch("app.services.llm_client.stream_chat", fake_stream_chat):
        [c async for c in ai_service.call_cloud_stream(provider, "hi", db=None)]

    with patch("app.services.ollama_client.chat_stream", fake_local_chat_stream):
        [c async for c in ai_service.call_ollama_stream("hi", db=None)]

    assert seen["cloud"] == settings.ai_max_output_tokens
    assert seen["local"] == settings.ai_max_output_tokens
