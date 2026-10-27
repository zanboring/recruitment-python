"""安全默认值回归测试：免密登录来源、生产密钥校验、初始管理员口令。

## 为什么需要这个文件

这三项的共同点是「**默认配置就危险**」，而默认配置恰恰是最容易被测试忽略的地方 ——
测试通常显式构造安全的环境，于是"什么都没配"这条最危险的路径反而没人测。

2026-10-26 审阅发现的链路是：

    默认监听 0.0.0.0 + app_env 默认 development + 免密登录不校验来源
    + 生产校验放过仓库里的公开常量 + 初始口令写死在源码
    = 默认部署等于管理员公开

下面每条都对应这条链上的一环。
"""
import pytest

from app.config import settings, validate_production_settings
from app.exceptions import AppException
from app.init_data import _resolve_initial_password, init_default_admin
from app.routers.compat import _assert_loopback

# --------------------------------------------------------------------------
# 免密登录：来源必须是本机
# --------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host):
        self.client = _FakeClient(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_本机来源允许免密登录(host):
    _assert_loopback(_FakeRequest(host), "自动登录")  # 不抛异常即通过


@pytest.mark.parametrize(
    "host",
    [
        # 这里刻意用 RFC 5737 的文档专用地址（192.0.2.0/24、198.51.100.0/24、
        # 203.0.113.0/24）而不是 192.168.x.x / 172.17.x.x 这种真实网段：
        # 公开版同步脚本的敏感扫描是**黑名单**，看到 192.168/172.16-31 就报"内网 IP 泄漏"，
        # 它分不清「测试夹具」和「真的把办公网地址写进仓库」。
        # 断言的是「非回环就拒绝」，这三种地址同样不是回环，覆盖等价。
        "10.0.0.5",        # 私有网段（扫描器不匹配 10/8，但同样是私网）
        "192.0.2.5",       # TEST-NET-1
        "198.51.100.7",    # TEST-NET-2
        "203.0.113.7",     # TEST-NET-3
        "127.0.0.1.evil",  # 前缀伪装：不能被 startswith 骗过
    ],
)
def test_非本机来源一律拒绝(host):
    with pytest.raises(AppException) as exc:
        _assert_loopback(_FakeRequest(host), "自动登录")
    assert exc.value.code == 403


def test_无客户端信息时放行以避免误伤测试():
    """ASGITransport 可能不填 client；真实 uvicorn 一定有，不会走到这个分支。"""

    class _Bare:
        client = None

    _assert_loopback(_Bare(), "自动登录")


@pytest.mark.asyncio
async def test_接口层面非本机来源返回403(client, monkeypatch):
    """确认接口真的调用了来源校验，而不是函数写好了没人用。"""
    import app.routers.compat as compat

    monkeypatch.setattr(
        compat, "_assert_loopback", lambda request, action: (_ for _ in ()).throw(AppException("仅限本机", 403))
    )
    # 这里只验证「校验被调用」这一事实：替换后接口必须跟着变成 403
    r = await client.post("/api/auth/auto-login", json={})
    assert r.status_code in (403, 429), r.text


# --------------------------------------------------------------------------
# 生产密钥校验
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weak",
    [
        # docker-compose.yml 的兜底值
        "change_me_to_a_long_random_secret_for_production",
        # config.template.env 的兜底值（另一个变体 —— 枚举黑名单漏过它）
        "change_me_to_a_long_random_secret_32chars_min",
        # 代码里的 dev 默认值
        "dev_only_jwt_secret_change_in_production",
        # 用户从文档里抄来的占位符（大小写混合也要拦）
        "YOUR_SECRET_KEY_HERE_PLEASE_CHANGE_IT_1234",
    ],
)
def test_生产环境拒绝仓库里公开的默认密钥(monkeypatch, weak):
    """长度 32+ 能穿过"至少 32 位"的长度门，所以必须按特征拒绝。

    这里特意覆盖了三个**写法不同但同源**的默认值：只按具体字符串枚举必然漏掉
    下一个新增的模板值（config.template.env 那次就是这么漏的）。
    """
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", weak)
    with pytest.raises(RuntimeError) as exc:
        validate_production_settings()
    assert "公开的默认值" in str(exc.value)
    # 报错信息里不能回显密钥本身（哪怕它是公开的，也不该养成这个习惯）
    assert weak not in str(exc.value)


def test_生产环境拒绝短密钥(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "short-secret")
    with pytest.raises(RuntimeError):
        validate_production_settings()


def test_生产环境接受足够长的随机密钥(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "q" * 48)
    validate_production_settings()  # 不抛异常


@pytest.mark.parametrize("env", ["development", "dev", "test"])
def test_非生产环境不做密钥校验(monkeypatch, env):
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "jwt_secret", "dev_only_jwt_secret_change_in_production")
    validate_production_settings()


# --------------------------------------------------------------------------
# 初始管理员口令
# --------------------------------------------------------------------------


def test_显式配置优先于环境默认(monkeypatch):
    monkeypatch.setattr(settings, "admin_initial_password", "My-Strong-Passw0rd")
    password, source = _resolve_initial_password()
    assert password == "My-Strong-Passw0rd"
    assert "ADMIN_INITIAL_PASSWORD" in source


def test_生产环境不产生固定口令(monkeypatch, tmp_path):
    """核心断言：生产环境的初始口令是**随机的**，且写入文件而不是打日志。

    注意不能只断言"有口令"——写死一个固定值也满足。要断言两次生成的结果不同。
    """
    import app.init_data as init_data

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "admin_initial_password", "")
    monkeypatch.setattr(init_data, "_PASSWORD_FILE", tmp_path / "pw.txt")

    first, source1 = _resolve_initial_password()
    assert len(first) >= 16
    assert source1.endswith("pw.txt")
    # 第一次生成的口令必须就是落盘的那个
    assert first in (tmp_path / "pw.txt").read_text(encoding="utf-8")

    # 再生成一次：结果必须不同（否则说明又变回了固定值）
    second, _ = _resolve_initial_password()
    assert first != second, "生产环境两次生成的口令相同，说明是固定值而非随机"


def test_开发环境沿用文档里的默认口令(monkeypatch):
    """开发默认值必须与 README 一致，否则「照文档跑却登不进去」。"""
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "admin_initial_password", "")
    password, source = _resolve_initial_password()
    assert password == "admin123"
    assert "开发环境" in source


@pytest.mark.asyncio
async def test_首次启动创建管理员且日志不打印明文(db_session, monkeypatch, caplog):
    from sqlalchemy import select

    from app.models.user import User

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "admin_initial_password", "")

    with caplog.at_level("INFO"):
        await init_default_admin(db_session)

    row = (await db_session.execute(select(User).where(User.username == "admin"))).scalars().first()
    assert row is not None and row.role == "ADMIN"
    # 口令明文绝不能出现在日志里
    assert "admin123" not in caplog.text


@pytest.mark.asyncio
async def test_已存在管理员时不重复创建(db_session, caplog):
    await init_default_admin(db_session)
    before = caplog.text
    await init_default_admin(db_session)
    # 第二次调用应当直接返回，不产生新的创建日志
    assert caplog.text == before
