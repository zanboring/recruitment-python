"""启动绑定：只拒绝真正危险的「弱密钥 + 非回环」组合。

## 为什么是两个条件的与，而不是任一

- **只判密钥弱**：双击即用的开发场景会被拦掉（默认密钥是刻意的演示默认），
  而回环监听时外部根本够不着接口，风险不成立。
- **只判非回环**：放过真正的杀手 —— 默认 `JWT_SECRET` 是仓库里公开的常量，
  任何读过源码的人都能离线签发 `{sub:"1", role:"ADMIN"}`，不需要知道口令。

2026-11 的审计（qoder P0-1）指出的链路正是「`start.bat` 绑 `0.0.0.0`」+
「密钥校验只在 `APP_ENV=production` 才跑」：默认配置下等于管理员公开。
两个 bat 已经改回 `127.0.0.1`；本文件锁的是第二道 —— 有人显式设
`SERVER_HOST=0.0.0.0` 时，弱密钥必须拒绝启动，而不是安静地对外服务。
"""
import pytest

from app.config import is_weak_jwt_secret
from run_entry import _is_loopback, _refuse_reason

STRONG_SECRET = "q" * 48
DEFAULT_SECRET = "dev_only_jwt_secret_change_in_production"


# --------------------------------------------------------------------------
# 回环判定
# --------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_回环地址判定(host):
    assert _is_loopback(host) is True


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.10", "::", "127.0.0.1.evil.com", "example.com", ""],
)
def test_非回环地址判定(host):
    """``127.0.0.1.evil.com`` 是重点。

    ``startswith("127.")`` 会把它判成回环，而该域名可以解析到任意地址 ——
    这正是本项目 `_assert_loopback` 早已改掉的写法，这里是同一判据的第二处。
    """
    assert _is_loopback(host) is False


# --------------------------------------------------------------------------
# 密钥强度判据
# --------------------------------------------------------------------------

def test_弱密钥判据同时看长度与特征():
    assert is_weak_jwt_secret(DEFAULT_SECRET) is True      # 精确命中默认值
    assert is_weak_jwt_secret("short") is True             # 长度不足
    assert is_weak_jwt_secret("CHANGE_ME_" + "x" * 40) is True   # 特征片段，大小写无关
    assert is_weak_jwt_secret(STRONG_SECRET) is False
    assert is_weak_jwt_secret(None) is True


# --------------------------------------------------------------------------
# 组合判定
# --------------------------------------------------------------------------

def test_非回环加弱密钥时拒绝启动():
    reason = _refuse_reason("0.0.0.0", DEFAULT_SECRET)
    assert reason and "拒绝启动" in reason
    # 报错信息不回显密钥本身（哪怕它是公开常量）
    assert DEFAULT_SECRET not in reason


def test_非回环但密钥够强时放行():
    assert _refuse_reason("0.0.0.0", STRONG_SECRET) is None


def test_回环地址用默认密钥也放行():
    """双击即用的开发场景不能被拦。"""
    assert _refuse_reason("127.0.0.1", DEFAULT_SECRET) is None
