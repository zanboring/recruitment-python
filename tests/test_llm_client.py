"""OpenAI 兼容调用层与多供应商容灾测试。

DeepSeek 与智谱共用同一份调用实现（`app/services/llm_client.py`），
这里用可控的假 HTTP 层验证三件事：

1. **请求构造正确** —— URL、模型名、认证头、思考模式参数；
2. **流式解析正确** —— 多分片拼接、`[DONE]` 终止、思考内容不透出、usage 回调；
3. **供应商优先级与容灾顺序** —— 主服务商密钥缺失时备选顶替。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.llm_client import CloudProvider, get_cloud_providers, stream_chat


class _FakeStreamResponse:
    """模拟 httpx 的流式响应上下文管理器。"""

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


class _FakeAsyncClient:
    """记录请求参数并回放预设的 SSE 分片。"""

    recorded: dict = {}
    sse_lines: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None):
        _FakeAsyncClient.recorded = {
            "method": method, "url": url, "headers": headers, "payload": json,
        }
        return _FakeStreamResponse(_FakeAsyncClient.sse_lines)


def _provider(**overrides) -> CloudProvider:
    base = dict(
        name="DeepSeek",
        provider="deepseek",
        api_url="https://api.deepseek.com/chat/completions",
        api_key="sk-test-key",
        model="deepseek-flash",
    )
    base.update(overrides)
    return CloudProvider(**base)


def _cfg(**overrides) -> SimpleNamespace:
    base = dict(
        ai_provider="deepseek",
        deepseek_api_key="",
        deepseek_api_url="https://api.deepseek.com/chat/completions",
        deepseek_model="deepseek-flash",
        deepseek_reasoning_effort="none",
        zhipuai_api_key="",
        zhipuai_api_url="https://open.bigmodel.cn/api/paas/v4/chat/completions",
        zhipuai_model="glm-4-flash",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ 请求构造

@pytest.mark.asyncio
async def test_请求构造包含端点模型名与认证头():
    _FakeAsyncClient.sse_lines = ['data: [DONE]']
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        async for _ in stream_chat(_provider(), [{"role": "user", "content": "hi"}]):
            pass

    req = _FakeAsyncClient.recorded
    assert req["method"] == "POST"
    assert req["url"] == "https://api.deepseek.com/chat/completions"
    assert req["payload"]["model"] == "deepseek-flash"
    assert req["headers"]["Authorization"] == "Bearer sk-test-key"
    assert req["payload"]["stream"] is True


@pytest.mark.asyncio
async def test_请求开启_usage_随流返回():
    """流式默认不带 token 用量，必须显式开启，否则无法做成本统计。"""
    _FakeAsyncClient.sse_lines = ['data: [DONE]']
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        async for _ in stream_chat(_provider(), [{"role": "user", "content": "hi"}]):
            pass

    assert _FakeAsyncClient.recorded["payload"]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_思考模式参数按配置注入():
    provider = _provider(extra_payload={"reasoning_effort": "high"})
    _FakeAsyncClient.sse_lines = ['data: [DONE]']
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        async for _ in stream_chat(provider, [{"role": "user", "content": "hi"}]):
            pass

    assert _FakeAsyncClient.recorded["payload"]["reasoning_effort"] == "high"


# ------------------------------------------------------------------ 流式解析

@pytest.mark.asyncio
async def test_流式解析拼接分片并忽略思考内容():
    """DeepSeek 思考模式下 delta 里有 reasoning_content，不应透出给用户。"""
    _FakeAsyncClient.sse_lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"（内部思考过程）"}}]}',
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: [DONE]',
    ]
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        chunks = [
            piece
            async for piece in stream_chat(_provider(), [{"role": "user", "content": "hi"}])
        ]

    assert "".join(chunks) == "你好"


@pytest.mark.asyncio
async def test_遇到_DONE_即停止解析():
    _FakeAsyncClient.sse_lines = [
        'data: {"choices":[{"delta":{"content":"A"}}]}',
        'data: [DONE]',
        'data: {"choices":[{"delta":{"content":"不应被读取"}}]}',
    ]
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        chunks = [
            piece
            async for piece in stream_chat(_provider(), [{"role": "user", "content": "hi"}])
        ]

    assert "".join(chunks) == "A"


@pytest.mark.asyncio
async def test_畸形分片不影响整体解析():
    """上游偶发返回非 JSON 行时，应跳过而不是让整条流崩掉。"""
    _FakeAsyncClient.sse_lines = [
        "data: {这不是合法JSON}",
        "",
        'data: {"choices":[{"delta":{"content":"正常"}}]}',
        'data: [DONE]',
    ]
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        chunks = [
            piece
            async for piece in stream_chat(_provider(), [{"role": "user", "content": "hi"}])
        ]

    assert "".join(chunks) == "正常"


@pytest.mark.asyncio
async def test_usage_回调被触发():
    """为成本统计预留的钩子：必须能拿到 token 用量。"""
    captured = []
    _FakeAsyncClient.sse_lines = [
        'data: {"choices":[{"delta":{"content":"x"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}',
        'data: [DONE]',
    ]
    with patch("app.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        async for _ in stream_chat(
            _provider(),
            [{"role": "user", "content": "hi"}],
            on_usage=captured.append,
        ):
            pass

    assert captured and captured[0]["total_tokens"] == 15


# ------------------------------------------------------------------ 供应商优先级

class TestProviderPriority:
    def test_未配置密钥时返回空列表(self):
        assert get_cloud_providers(_cfg()) == []

    def test_默认_deepseek_优先于智谱(self):
        cfg = _cfg(deepseek_api_key="k1", zhipuai_api_key="k2")
        assert [p.provider for p in get_cloud_providers(cfg)] == ["deepseek", "zhipu"]

    def test_主服务商缺密钥时备选顶替(self):
        """多供应商容灾：主服务商密钥过期时，备选应当自动顶上。"""
        cfg = _cfg(deepseek_api_key="", zhipuai_api_key="k2")
        providers = get_cloud_providers(cfg)
        assert [p.provider for p in providers] == ["zhipu"]

    def test_可配置智谱优先(self):
        cfg = _cfg(deepseek_api_key="k1", zhipuai_api_key="k2", ai_provider="zhipu")
        assert [p.provider for p in get_cloud_providers(cfg)] == ["zhipu", "deepseek"]

    def test_思考模式配置被翻译成请求参数(self):
        cfg = _cfg(deepseek_api_key="k1", deepseek_reasoning_effort="high")
        assert get_cloud_providers(cfg)[0].extra_payload == {"reasoning_effort": "high"}

    def test_思考模式关闭时不注入参数(self):
        cfg = _cfg(deepseek_api_key="k1", deepseek_reasoning_effort="none")
        assert get_cloud_providers(cfg)[0].extra_payload == {}
