"""流式主链路的熔断记账测试（W-3）。

**背景**：`complete_chat` 成功/失败都会 record_success / record_failure，
但 `stream_chat` 只**检查**熔断状态、从不**上报**结果。而聊天主入口
`/api/ai/chat-stream` 走的正是流式路径 —— 于是 provider 连续失败时
熔断器永远停在 CLOSED，每个请求都照样硬打一遍（且每次都要等满超时）。

本文件验证五件事：
1. 连续 N 次流式失败 → 熔断器 OPEN；
2. OPEN 之后**不再真正发起请求**（替身计数证明）；
3. 正常跑完 → record_success（失败计数归零）；
4. 客户端提前断开（GeneratorExit）**不算失败**；
5. `AI_CIRCUIT_BREAKER_ENABLED=false` 时行为与改造前一致（不记账、不拦截）。
"""
import httpx
import pytest

from app.services import llm_client
from app.services.llm_client import CircuitOpenError, CloudProvider, stream_chat
from app.utils.circuit_breaker import CircuitBreaker, CircuitState
from tests.helpers import FakeLineIterator


def _provider() -> CloudProvider:
    return CloudProvider(
        name="DeepSeek",
        provider="deepseek",
        api_url="https://api.deepseek.com/chat/completions",
        api_key="sk-test-key",
        model="deepseek-chat",
    )


class _CountingClient:
    """记录「真正发起过多少次请求」；`behavior` 可中途替换以切换成功/失败。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None):
        self.calls += 1
        return self.behavior()


class _OkStream:
    """成功的流式响应替身。"""

    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    def aiter_lines(self):
        return FakeLineIterator(self._lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fail_connect():
    """模拟连接失败（DNS 失败 / 拒绝连接 / 超时同一入口）。"""
    raise httpx.ConnectError("connection refused")


def _fail_status():
    """模拟 HTTP 500：raise_for_status 抛 HTTPStatusError。"""
    class _BadStream(_OkStream):
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "500", request=httpx.Request("POST", "https://x"), response=httpx.Response(500)
            )

    return _BadStream([])


def _ok_stream(text="你好"):
    return _OkStream([
        'data: {"choices":[{"delta":{"content":"%s"}}]}' % text,
        "data: [DONE]",
    ])


@pytest.fixture
def breaker(monkeypatch):
    """每个用例换一个干净的熔断器实例，避免用例之间互相污染。"""
    b = CircuitBreaker("test-llm", failure_threshold=3, cooldown_seconds=60)
    monkeypatch.setattr(llm_client, "_cloud_breaker", b)
    monkeypatch.setattr(llm_client.settings, "ai_circuit_breaker_enabled", True)
    return b


def _install(monkeypatch, client):
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda *a, **k: client)
    return client


async def _consume(provider=None):
    chunks = []
    async for chunk in stream_chat(provider or _provider(), [{"role": "user", "content": "hi"}]):
        chunks.append(chunk)
    return chunks


# --------------------------------------------------------------------------
# 1 / 2. 连续失败 → OPEN；OPEN 后不再真调用
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_连续流式失败会打开熔断器(monkeypatch, breaker):
    client = _install(monkeypatch, _CountingClient(_fail_connect))

    for _ in range(3):  # failure_threshold = 3
        with pytest.raises(httpx.ConnectError):
            await _consume()

    assert breaker.state == CircuitState.OPEN
    assert client.calls == 3
    assert breaker.stats()["failure"] == 3


@pytest.mark.asyncio
async def test_HTTP状态异常同样计入失败(monkeypatch, breaker):
    client = _install(monkeypatch, _CountingClient(_fail_status))

    for _ in range(3):
        with pytest.raises(httpx.HTTPStatusError):
            await _consume()

    assert breaker.state == CircuitState.OPEN
    assert client.calls == 3


@pytest.mark.asyncio
async def test_熔断打开后不再真正发起请求(monkeypatch, breaker):
    """核心判据：打开后调用次数**不再增长**（快速失败，不是又打了一遍）。"""
    client = _install(monkeypatch, _CountingClient(_fail_connect))

    for _ in range(3):
        with pytest.raises(httpx.ConnectError):
            await _consume()
    assert breaker.state == CircuitState.OPEN
    calls_when_opened = client.calls

    with pytest.raises(CircuitOpenError):
        await _consume()

    assert client.calls == calls_when_opened, "熔断期间不应再发起真实请求"


# --------------------------------------------------------------------------
# 3. 成功路径
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_流式正常结束记为成功并清零失败计数(monkeypatch, breaker):
    client = _install(monkeypatch, _CountingClient(_fail_connect))

    for _ in range(2):  # 未达阈值
        with pytest.raises(httpx.ConnectError):
            await _consume()
    assert breaker.stats()["failure"] == 2

    client.behavior = _ok_stream
    assert await _consume() == ["你好"]

    assert breaker.state == CircuitState.CLOSED
    assert breaker.stats()["success"] == 1
    # 成功后失败计数必须归零 —— 否则「隔三差五失败」会累积到误熔断
    assert breaker._failure_count == 0


# --------------------------------------------------------------------------
# 4. 客户端断开：不能记成 provider 故障
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_客户端提前断开不算失败(monkeypatch, breaker):
    _install(monkeypatch, _CountingClient(lambda: _ok_stream("第一段")))

    agen = stream_chat(_provider(), [{"role": "user", "content": "hi"}])
    assert await agen.__anext__() == "第一段"
    await agen.aclose()  # 等价于客户端断开 / 任务取消

    assert breaker.stats()["failure"] == 0, "断开不是上游故障，不能记账"
    assert breaker.stats()["success"] == 0
    assert breaker.state == CircuitState.CLOSED


# --------------------------------------------------------------------------
# 5. 开关关闭时行为不变
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_关闭熔断开关时不记账也不拦截(monkeypatch, breaker):
    monkeypatch.setattr(llm_client.settings, "ai_circuit_breaker_enabled", False)
    client = _install(monkeypatch, _CountingClient(_fail_connect))

    for _ in range(5):  # 远超阈值
        with pytest.raises(httpx.ConnectError):
            await _consume()

    assert client.calls == 5, "关闭后不应拦截"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.stats()["failure"] == 0, "关闭后不应记账"
