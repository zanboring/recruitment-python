"""URL 导入的 SSRF 防护测试（W-1）。

覆盖四类必须拒绝的输入：
1. **回环**：127.0.0.1 / localhost / [::1] / 2130706433（十进制写法）
2. **私有网段**：10/8、172.16/12、192.168/16、100.64/10
3. **云厂商元数据**：169.254.169.254（链路本地）、metadata.google.internal
4. **重定向到内网**：首跳公网 → Location 指向 127.0.0.1

额外覆盖：公网域名**解析后落到内网**（只看字符串永远发现不了）、
非 http/https scheme、公网地址正常放行、相对 Location、重定向次数上限。

**测试全程离线**：`url_safety._resolve` 被注入替身，不依赖真实 DNS；
这也让我们能构造「公网域名 → 内网 IP」这种真实攻击形态。
"""
import ipaddress

import pytest

from app.services import url_import_service
from app.utils import url_safety
from app.utils.url_safety import UnsafeURLError, assert_safe_url, safe_get
from tests.helpers import admin_token, auth_headers


def _make_resolver(mapping):
    """构造解析替身。

    - 主机名本身是 IP 字面量 → 原样返回（模拟操作系统行为，保持确定性）；
    - 否则查 mapping；`"*"` 表示默认值；查不到给一个真实公网地址。
    """

    def _fake(host, port):
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        if "*" in mapping:
            return [mapping["*"]]
        return [mapping.get(host, "93.184.216.34")]

    return _fake


@pytest.fixture
def dns(monkeypatch):
    """dns() / dns("10.0.0.5") / dns({"jobs.example.com": "10.0.0.5"})。"""

    def install(mapping=None):
        if isinstance(mapping, str):
            mapping = {"*": mapping}
        monkeypatch.setattr(url_safety, "_resolve", _make_resolver(mapping or {}))

    return install


# --------------------------------------------------------------------------
# 1. 回环
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/api/system/version",
        "http://[::1]:8080/",
        "http://0.0.0.0:8080/",
    ],
)
def test_回环地址被拒(url, dns):
    dns()
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url(url)


def test_省略写法的回环被拒(dns):
    """`127.1` 是 `127.0.0.1` 的省略写法，由系统解析器补齐（这里用替身模拟该行为）。"""
    dns("127.0.0.1")
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://127.1/")


def test_localhost域名被拒(dns):
    dns("127.0.0.1")
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://localhost:8080/")


def test_十进制回环写法被拒(dns):
    """2130706433 == 127.0.0.1；靠字符串前缀判断会漏掉这种写法。"""
    dns("127.0.0.1")
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://2130706433/")


def test_前缀像回环的公网域名放行(dns):
    """`127.0.0.1.evil.com` 是**公网域名**，不该被前缀规则误杀 —— 所以必须解析后判。"""
    dns({"127.0.0.1.evil.com": "93.184.216.34"})
    assert assert_safe_url("http://127.0.0.1.evil.com/")


# --------------------------------------------------------------------------
# 2. 私有网段
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ip", ["10.0.0.5", "172.16.3.9", "192.168.1.1", "100.64.1.1"])
def test_私有网段被拒(ip, dns):
    """公网域名解析到内网 IP —— 这是关键一类：URL 字符串完全无害。"""
    dns(ip)
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://internal.example.com/")


# --------------------------------------------------------------------------
# 3. 云厂商元数据
# --------------------------------------------------------------------------


def test_元数据地址被拒(dns):
    dns("169.254.169.254")
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://metadata.google.internal/computeMetadata/v1/")


def test_元数据IP字面量被拒(dns):
    dns()
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://169.254.169.254/latest/meta-data/")


def test_IPv6映射的回环被拒(dns):
    """::ffff:127.0.0.1 是回环的 IPv6 映射写法，不能漏网。"""
    dns()
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        assert_safe_url("http://[::ffff:127.0.0.1]/")


# --------------------------------------------------------------------------
# 4. 非 http/https
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/win.ini",
        "ftp://example.com/x",
        "gopher://example.com:70/_test",
        "javascript:alert(1)",
        "//example.com/path",
    ],
)
def test_非http协议被拒(url, dns):
    dns()
    with pytest.raises(UnsafeURLError, match="合法的 http/https"):
        assert_safe_url(url)


# --------------------------------------------------------------------------
# 5. 公网放行（避免误杀正常导入）
# --------------------------------------------------------------------------


def test_公网域名放行(dns):
    dns()
    assert assert_safe_url("https://jobs.example.com/job/1") == "https://jobs.example.com/job/1"


def test_公网IP字面量放行(dns):
    dns()
    assert assert_safe_url("http://93.184.216.34/job/1") == "http://93.184.216.34/job/1"


# --------------------------------------------------------------------------
# 6. 重定向：必须逐跳校验，且内网目标**从未被请求**
# --------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.closed = False

    async def aclose(self):
        self.closed = True


class _FakeClient:
    """`safe_get` 内部创建的 httpx.AsyncClient 替身；记录**真实发出**的请求。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.requested.append(url)
        return self._responses.pop(0)


def _patch_httpx(monkeypatch, client):
    """让 safe_get 内部的 `httpx.AsyncClient(...)` 返回给定替身。"""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    return client


@pytest.mark.asyncio
async def test_重定向到内网被拒_且内网地址从未被请求(dns, monkeypatch):
    """核心断言：不是「请求完再判」，而是**先判后发** —— 第二跳根本没发出去。"""
    dns({"jobs.example.com": "93.184.216.34"})
    client = _patch_httpx(
        monkeypatch,
        _FakeClient([_FakeResp(302, headers={"location": "http://127.0.0.1:8080/admin"})]),
    )

    with pytest.raises(UnsafeURLError, match="内网/本机"):
        await safe_get("http://jobs.example.com/job/1")

    assert client.requested == ["http://jobs.example.com/job/1"]


@pytest.mark.asyncio
async def test_重定向到公网正常跟随(dns, monkeypatch):
    dns()
    client = _patch_httpx(
        monkeypatch,
        _FakeClient(
            [
                _FakeResp(301, headers={"location": "https://m.example.com/job/1"}),
                _FakeResp(200, text="<html><body>ok</body></html>"),
            ]
        ),
    )
    resp = await safe_get("http://jobs.example.com/job/1")
    assert resp.status_code == 200
    assert client.requested == ["http://jobs.example.com/job/1", "https://m.example.com/job/1"]


@pytest.mark.asyncio
async def test_相对Location按当前地址解析(dns, monkeypatch):
    dns()
    client = _patch_httpx(
        monkeypatch,
        _FakeClient([_FakeResp(302, headers={"location": "/login"}), _FakeResp(200, text="ok")]),
    )
    await safe_get("http://jobs.example.com/job/1")
    assert client.requested[1] == "http://jobs.example.com/login"


@pytest.mark.asyncio
async def test_重定向次数超限被拒(dns, monkeypatch):
    dns()
    _patch_httpx(
        monkeypatch,
        _FakeClient([_FakeResp(302, headers={"location": "/loop"}) for _ in range(10)]),
    )
    with pytest.raises(UnsafeURLError, match="重定向次数"):
        await safe_get("http://jobs.example.com/")


# --------------------------------------------------------------------------
# 7. 入口与接口层：拒绝时返回 4xx + 明确原因
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_导入入口直接拒绝内网链接():
    """不依赖任何桩：127.0.0.1 是 IP 字面量，解析不需要网络。"""
    with pytest.raises(UnsafeURLError, match="内网/本机"):
        await url_import_service.import_job_from_url(None, "http://127.0.0.1:8080/api/system/version")


@pytest.mark.asyncio
async def test_接口层返回400且带明确原因(client, db_session, dns):
    dns("192.168.1.10")
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/jobs/url-import",
        json={"url": "http://intranet.example.com/job/1"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 400
    assert "内网" in resp.json()["message"]
