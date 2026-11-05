# -*- coding: utf-8 -*-
'PyInstaller 打包入口：启动单进程全栈服务。'
import os
import sys
from pathlib import Path

# 打包后 exe 同目录作为工作目录（确保 data/、config.env 可读写）
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).resolve().parent)

# 通用版默认 SQLite 库在 data/ 下；该目录可能不存在（首次启动的干净机器），
# 必须自动创建，否则 sqlite3 报 "unable to open database file" 直接闪退。
_d = Path(os.getcwd()) / "data"
try:
    _d.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # 只读目录等极端情况：交由后续数据库逻辑报更明确的错误

from app.main import create_app  # noqa: E402


def _is_loopback(host: str) -> bool:
    """判「只在本机可访问」。

    用 ``ipaddress.is_loopback`` 而不是 ``host.startswith("127.")``：
    后者会把 ``127.0.0.1.evil.com`` 这类域名判成回环（该域名可解析到任意 IP）。
    解析不了的字符串按「非回环」处理 —— 保守方向。
    """
    import ipaddress

    value = (host or "").strip()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _refuse_reason(host: str, jwt_secret: str | None) -> str | None:
    """「弱密钥 + 非回环」才拒绝启动，返回拒绝理由；放行时返回 ``None``。

    两个条件缺一不可：

    * 只判密钥弱 —— 会把双击即用的开发场景也拦掉（默认密钥是刻意的演示默认），
      而回环监听时外部根本够不着接口；
    * 只判非回环 —— 放过了真正的杀手：默认 JWT_SECRET 是仓库里公开的常量，
      任何读过源码的人都能离线签发 ``{sub:"1", role:"ADMIN"}``，不需要知道口令。

    抽成纯函数是为了可测 —— 逻辑埋在 ``if __name__ == "__main__"`` 里就只能靠
    真实启动进程来验，那样的用例既慢又容易变成假判据。
    """
    from app.config import is_weak_jwt_secret

    if _is_loopback(host):
        return None
    if not is_weak_jwt_secret(jwt_secret):
        return None
    return (
        f"[RecSys] 拒绝启动：监听 {host}（非回环）却使用默认/弱 JWT_SECRET。\n"
        "         这等于把管理员权限公开给同网段任意设备 —— 密钥是仓库里公开的常量，\n"
        "         任何人都能离线伪造 Token。\n"
        "         解决：设置强随机密钥后重试\n"
        '           set JWT_SECRET=<32位以上随机串>\n'
        '           生成：python -c "import secrets;print(secrets.token_urlsafe(48))"'
    )


if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    port = int(os.getenv("SERVER_PORT", "8080"))
    # 默认只监听回环地址。
    #
    # 原先写死 "0.0.0.0"：这是个单机桌面软件（打包成 exe 双击运行），
    # 没有任何"对外提供服务"的需求，但它把全部接口——包括免密登录
    # /api/auth/auto-login——暴露给同一个局域网内的任意设备。
    # 在咖啡厅/学校的公共 WiFi 下，这等于把管理员权限挂在网上。
    #
    # 需要局域网访问（手机上看、容器里跑）时显式设置 SERVER_HOST=0.0.0.0，
    # 并自己确认网络环境可信。
    host = os.getenv("SERVER_HOST", "127.0.0.1")

    refusal = _refuse_reason(host, settings.jwt_secret)
    if refusal:
        raise SystemExit(refusal)

    print(f"[RecSys] 监听 http://{host}:{port}（如需局域网访问，设置 SERVER_HOST=0.0.0.0）")
    uvicorn.run(create_app(), host=host, port=port)
