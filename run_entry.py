# -*- coding: utf-8 -*-
'PyInstaller 打包入口：启动单进程全栈服务。'
import os
import sys
from pathlib import Path

# 打包后 exe 同目录作为工作目录（确保 data/、config.env 可读写）
if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).resolve().parent)

from app.main import create_app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SERVER_PORT", "8080"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
