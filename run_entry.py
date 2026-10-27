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

if __name__ == "__main__":
    import uvicorn

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
    print(f"[RecSys] 监听 http://{host}:{port}（如需局域网访问，设置 SERVER_HOST=0.0.0.0）")
    uvicorn.run(create_app(), host=host, port=port)
