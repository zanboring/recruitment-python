# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单进程全栈可移植软件
# 用法：build-exe.bat（或 pyinstaller recsys.spec --noconfirm）
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

a = Analysis(
    ['run_entry.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 前端构建产物（npm run build 后存在；缺失时打包回落 app/static）
        ('frontend/dist/index.html', 'frontend_dist'),
        ('frontend/dist/assets', 'frontend_dist/assets'),
        ('app/static', 'app_static'),
    ],
    hiddenimports=[
        # SQLAlchemy 动态方言
        'aiosqlite', 'aiomysql', 'asyncpg',
        'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
        # 可选依赖标记（不强制安装）
        'openpyxl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Playwright 太大（chromium ~300MB），链接导入降级为提示；爬虫需手动装
        'playwright', '_pytest', 'pytest', 'matplotlib', 'numpy', 'pandas',
        'pydantic_core._pytest_plugin',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RecSys',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台便于查看日志；想隐藏改 False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='RecSys',
)
