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
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
