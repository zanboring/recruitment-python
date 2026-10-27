@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   打包 RecSys 可移植软件（PyInstaller）
echo   前置：Python 3.11+ / Node.js 20+（首次）
echo ============================================

rem ---- 0. 前置检查 ----
where python >nul 2>nul || (echo 未检测到 Python & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
    echo 创建虚拟环境...
    python -m venv .venv
)

rem ---- 1. 构建前端（需要时）----
if exist "frontend\package.json" (
    echo 构建前端...
    pushd frontend
    call npm install --no-fund --no-audit >nul 2>&1
    call npm run build
    popd
)

rem ---- 2. 安装打包依赖与运行依赖 ----
echo 安装依赖（首次较慢）...
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller -q

rem ---- 3. 打包前校验：依赖必须齐全，否则打出的 exe 会在启动时崩 ----
rem  真实事故：曾用一个只装了 pyinstaller、没装 aiomysql 的环境打包，
rem  产物启动即 ModuleNotFoundError: No module named 'aiomysql'。
rem  原因是 app/database.py 里 db_type 默认 mysql，驱动是运行时按方言动态 import 的，
rem  PyInstaller 的静态分析看不到它；一旦环境里没装，hiddenimports 写了也打不进去。
echo 校验运行时依赖...
".venv\Scripts\python.exe" -c "import aiosqlite, aiomysql, asyncpg, fastapi, sqlalchemy, uvicorn, openpyxl" || (
    echo.
    echo [错误] 依赖不完整，打出的 exe 会在启动时崩溃。请先修复：
    echo        .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem ---- 4. 执行打包 ----
".venv\Scripts\python.exe" -m PyInstaller recsys.spec --noconfirm

rem ---- 5. 打包后校验：依赖真的进了产物 ----
rem  2026-10-26 修正：原判据「dist\RecSys\_internal\<模块> 是否存在」对纯 Python 包必然为假。
rem  原因：纯 Python 包被打进 exe 内嵌的 PYZ 归档，不产生 _internal 目录；
rem        只有带 C 扩展的包（asyncpg / pydantic_core）才会落成 _internal\<模块>\。
rem  实测：原判据会让「打包成功」被误报为失败（aiosqlite / aiomysql 永远「不存在」），
rem        而产物实际启动正常、GET / 返回 200。
rem  现改为调用 scripts\check_bundle.py，用 PyInstaller 官方 API 直接读 PYZ，对两类包都成立。
echo 校验产物完整性...
".venv\Scripts\python.exe" "scripts\check_bundle.py"
if errorlevel 1 (
    echo.
    echo [错误] 产物校验未通过，不要发布。请修复上面标 [错误] 的项。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成：dist\RecSys\RecSys.exe
echo   使用：复制 dist\RecSys 整个目录到目标电脑，
echo        在目录里放 config.env（见 config.template.env）填入 API Key
echo        双击 RecSys.exe 启动，访问 http://localhost:8080
echo ============================================
pause
