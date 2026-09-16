@echo off
rem ============================================================
rem  AI 招聘数据可视化系统 - 一键启动（Windows）
rem  
rem  使用方法：双击本文件
rem  效果：自动建 venv -> 装依赖 -> 建库+种子数据(可选) -> 
rem        启动服务 -> 自动打开浏览器 -> 单进程全栈
rem ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   AI 招聘数据可视化系统 启动器
echo ============================================
echo.

rem ---- 1. 检查 Python ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11+ 并加入 PATH
    pause
    exit /b 1
)

rem ---- 2. 创建虚拟环境（若不存在）----
if not exist ".venv\Scripts\python.exe" (
    echo [1/5] 首次运行：创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
set "PY=.venv\Scripts\python.exe"

rem ---- 3. 安装依赖（requirements 有变更时 pip 会跳过已装的）----
echo [2/5] 检查依赖...
"%PY%" -m pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

rem ---- 4. 初始化数据库 + 可选种子数据 ----
echo [3/5] 初始化数据库...
"%PY%" scripts/init_db.py
echo.

rem 种子数据：如需自动填充演示数据，去掉下一行的 rem
rem "%PY%" scripts/seed_demo_data.py 200

rem ---- 5. 启动服务 ----
echo [4/5] 启动服务（单进程全栈：API + 前端页面）...
echo     访问地址 http://localhost:8080
echo     停止服务：关闭本窗口即可
echo.
start "" http://localhost:8080
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8080

endlocal