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


# --------------------------------------------------------------------------
# `python -m app.main` 入口（2026-11-11 补）
#
# README 的快速演示让读者跑 `python -m app.main`，而 app/main.py 原先只有
# 模块级 ``app = create_app()``、没有 __main__ 分支 —— 命令执行完即退出，
# 服务不起。这里不靠"读源码 grep"断言，而是把该分支**真的执行一遍**
# （runpy 以 __main__ 名义跑模块，uvicorn.run 打桩），否则测的是文本不是行为。
# --------------------------------------------------------------------------

def _run_module_main(monkeypatch, env: dict) -> dict:
    """执行 app/main.py 的 __main__ 分支，返回传给 uvicorn.run 的实参。"""
    import runpy

    import uvicorn

    for key in ("SERVER_HOST", "SERVER_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    captured: dict = {}

    def fake_run(app_obj, **kwargs):
        captured["app"] = app_obj
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    runpy.run_module("app.main", run_name="__main__")
    return captured


def test_模块入口默认只监听回环且端口8080(monkeypatch):
    captured = _run_module_main(monkeypatch, {})
    assert captured["host"] == "127.0.0.1", "默认必须只监听回环，不能是 0.0.0.0"
    assert captured["port"] == 8080
    assert "reload" not in captured, "生产入口不该带 reload"
    assert captured["app"] is not None


def test_模块入口的端口来自settings而不是硬编码(monkeypatch):
    """端口读 ``settings.server_port``：改了配置就该跟着变。

    注意这里 patch 的是 settings 对象而不是环境变量 ——
    ``Settings`` 是**导入时**实例化的单例，进程启动后再改环境变量不会回灌。
    真实用法（``SERVER_PORT=8099 python -m app.main``）在启动前就设好了，
    所以那条路径没问题；测试里要验"是否真的读配置"，只能改对象。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "server_port", 8097, raising=False)
    captured = _run_module_main(monkeypatch, {})
    assert captured["port"] == 8097


def test_端口配置项存在且默认为8080():
    """`server_port` 是 Settings 的正式字段（环境变量 SERVER_PORT 可注入）。"""
    from app.config import Settings

    assert Settings().server_port == 8080
    assert Settings(server_port=8095).server_port == 8095
