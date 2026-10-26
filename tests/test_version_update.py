"""版本自证与更新检查的测试。

重点是两类容易写错的逻辑：
1. **版本比较**不能用字典序（``"1.10.0" < "1.9.0"`` 在字典序下成立，会漏掉新版）；
2. **检查更新永不失败** —— 网络/限流/无 Release 都要以状态字段如实返回，
   而不是抛异常把接口打成 500。
"""
import pytest
import pytest_asyncio

from app.config import settings
from app.services import update_service
from app.version import APP_VERSION, build_info, is_frozen, is_newer, parse_version

from tests.helpers import admin_token, auth_headers

# --------------------------------------------------------------------------
# 版本解析与比较
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.0.0", (1, 0, 0)),
        ("v1.2.3", (1, 2, 3)),
        (" 1.2.3 ", (1, 2, 3)),
        ("1.2.3-beta.1", (1, 2, 3)),
        ("1.2.3+build.7", (1, 2, 3)),
        ("10.20.30", (10, 20, 30)),
    ],
)
def test_版本解析(raw, expected):
    assert parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "v1", "1.2", "abc", "1.2.x", "release-1.0.0"])
def test_无法解析的版本返回_None_而不抛异常(raw):
    assert parse_version(raw) is None


def test_版本比较必须按数字而不是字典序():
    """回归：字典序下 "1.10.0" < "1.9.0"，会把新版判成旧版。"""
    assert is_newer("1.10.0", "1.9.0") is True
    assert is_newer("1.9.0", "1.10.0") is False


def test_版本比较基本语义():
    assert is_newer("v1.0.1", "1.0.0") is True
    assert is_newer("1.1.0", "1.0.9") is True
    assert is_newer("2.0.0", "1.99.99") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("0.9.0", "1.0.0") is False


def test_任一方不可比较时不要误判为新版():
    """脏 tag 不能让用户看到一个「假的新版本」。"""
    assert is_newer("nightly", "1.0.0") is False
    assert is_newer("1.0.0", "unknown") is False
    assert is_newer(None, "1.0.0") is False


def test_构建信息包含发行形态():
    info = build_info()
    assert info["version"] == APP_VERSION
    assert info["distribution"] in ("source", "frozen-exe")
    assert info["name"]
    assert info["python"]
    assert info["checked_at"]
    # 未打包时必须是 source —— 前端据此给出「git pull」而不是「下载压缩包」的升级指引
    assert is_frozen() is False
    assert info["distribution"] == "source"


# --------------------------------------------------------------------------
# 仓库标识归一化
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("owner/name", "owner/name"),
        ("  owner/name  ", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("https://github.com/owner/name/", "owner/name"),
        ("http://github.com/owner/name", "owner/name"),
        ("git@github.com:owner/name.git", "owner/name"),
        ("owner/name.git", "owner/name"),
    ],
)
def test_仓库标识归一化(raw, expected):
    assert update_service.normalize_repo(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "owner", "a/b/c", "https://gitlab.com/a/b"])
def test_非法仓库标识被拒绝(raw):
    assert update_service.normalize_repo(raw) == ""


# --------------------------------------------------------------------------
# 检查更新（全部离线）
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def fake_github(monkeypatch):
    """替换 httpx.AsyncClient，记录请求。"""
    calls = []

    def install(status_code=200, payload=None, exc=None, raise_json=False):
        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, url, headers=None):
                calls.append({"url": url, "headers": headers or {}})
                if exc is not None:
                    raise exc
                return FakeResponse(status_code, payload, raise_json)

        monkeypatch.setattr(update_service.httpx, "AsyncClient", _Client)
        return calls

    return install


@pytest.fixture
def update_on(monkeypatch):
    monkeypatch.setattr(settings, "update_check_enabled", True)
    monkeypatch.setattr(settings, "update_repo", "zanboring/recruitment-python")


def release_payload(tag="v1.0.1", assets=None):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/zanboring/recruitment-python/releases/tag/{tag}",
        "published_at": "2026-09-20T10:00:00Z",
        "body": "修复若干问题",
        "assets": assets if assets is not None else [
            {"name": "RecSys-v1.0.1.zip", "browser_download_url": "https://example.com/a.zip"},
        ],
    }


@pytest.mark.asyncio
async def test_开关关闭时不发请求(fake_github, monkeypatch):
    calls = fake_github()
    monkeypatch.setattr(settings, "update_check_enabled", False)

    result = await update_service.check_for_update()

    assert result["status"] == "disabled"
    assert result["current_version"] == APP_VERSION
    assert calls == []


@pytest.mark.asyncio
async def test_仓库配置非法时不发请求(fake_github, monkeypatch):
    calls = fake_github()
    monkeypatch.setattr(settings, "update_check_enabled", True)
    monkeypatch.setattr(settings, "update_repo", "not-a-repo")

    result = await update_service.check_for_update()

    assert result["status"] == "unconfigured"
    assert calls == []


@pytest.mark.asyncio
async def test_发现新版本(fake_github, update_on):
    calls = fake_github(payload=release_payload("v1.0.1"))

    result = await update_service.check_for_update()

    assert result["status"] == "ok"
    assert result["has_update"] is True
    assert result["latest_version"] == "1.0.1"
    assert result["asset_name"] == "RecSys-v1.0.1.zip"
    assert "releases/tag" in result["release_url"]
    # 请求必须带 User-Agent，否则 GitHub 直接 403
    assert calls[0]["headers"].get("User-Agent")


@pytest.mark.asyncio
async def test_已是最新版本(fake_github, update_on):
    fake_github(payload=release_payload("v1.0.0"))
    result = await update_service.check_for_update()
    assert result["status"] == "ok"
    assert result["has_update"] is False


@pytest.mark.asyncio
async def test_本地比线上超前时不提示更新(fake_github, update_on):
    """开发中本地版本可能高于最新 Release，不该让用户「降级」。"""
    fake_github(payload=release_payload("v0.9.9"))
    result = await update_service.check_for_update()
    assert result["has_update"] is False


@pytest.mark.asyncio
async def test_还没有_Release_不算错误(fake_github, update_on):
    fake_github(status_code=404)
    result = await update_service.check_for_update()
    assert result["status"] == "no_release"


@pytest.mark.asyncio
async def test_被限流时如实告知(fake_github, update_on):
    fake_github(status_code=403)
    result = await update_service.check_for_update()
    assert result["status"] == "error"
    assert "限" in result["detail"]


@pytest.mark.asyncio
async def test_网络异常不得抛出(fake_github, update_on):
    fake_github(exc=RuntimeError("connection reset"))
    result = await update_service.check_for_update()
    assert result["status"] == "error"
    assert "connection reset" in result["detail"]


@pytest.mark.asyncio
async def test_响应不是_JSON_时不得抛出(fake_github, update_on):
    fake_github(raise_json=True)
    result = await update_service.check_for_update()
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_响应结构异常时不得抛出(fake_github, update_on):
    fake_github(payload=["unexpected"])
    result = await update_service.check_for_update()
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_没有附件时也能正常返回(fake_github, update_on):
    fake_github(payload=release_payload("v1.0.2", assets=[]))
    result = await update_service.check_for_update()
    assert result["status"] == "ok"
    assert result["has_update"] is True
    assert result["asset_name"] == ""


@pytest.mark.asyncio
async def test_有多个附件时优先挑_zip(fake_github, update_on):
    fake_github(payload=release_payload("v1.0.3", assets=[
        {"name": "checksums.txt"},
        {"name": "RecSys.zip"},
    ]))
    result = await update_service.check_for_update()
    assert result["asset_name"] == "RecSys.zip"


@pytest.mark.asyncio
async def test_超长版本说明被截断(fake_github, update_on):
    payload = release_payload("v1.0.4")
    payload["body"] = "长" * 9000
    fake_github(payload=payload)
    result = await update_service.check_for_update()
    assert len(result["notes"]) == update_service.MAX_NOTES_CHARS


# --------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_client(client, db_session):
    token = await admin_token(client, db_session, "admin_sysinfo")
    client.headers.update(auth_headers(token))
    return client


@pytest.mark.asyncio
async def test_版本接口要求登录(client):
    resp = await client.get("/api/system/version")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_版本接口返回版本与发行形态(admin_client):
    resp = await admin_client.get("/api/system/version")
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["version"] == APP_VERSION
    assert data["distribution"] == "source"
    # 不同发行形态的升级方式不同，提示文案必须区分
    assert "git pull" in data["upgrade_hint"]


@pytest.mark.asyncio
async def test_更新检查接口要求登录(client):
    resp = await client.get("/api/system/update-check")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_更新检查接口可用(fake_github, update_on, admin_client):
    fake_github(payload=release_payload("v1.0.5"))

    resp = await admin_client.get("/api/system/update-check")

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "ok"
    assert data["has_update"] is True
    assert data["latest_version"] == "1.0.5"


@pytest.mark.asyncio
async def test_OpenAPI_文档版本与代码一致(admin_client):
    """回归：版本号曾只写在 main.py 的字面量里，容易与 APP_VERSION 漂移。"""
    resp = await admin_client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["version"] == APP_VERSION
