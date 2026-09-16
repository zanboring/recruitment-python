@echo off
rem ============================================================
rem  通用版启动器（源码模式 / 绿色版，零 MySQL/Ollama 依赖）
rem  
rem  目标电脑只需：
rem    1. 安装 Python 3.11+（加入 PATH）
rem    2. 将 config.template.env 复制为 config.env 并填入 API Key
rem    3. 双击本文件
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul || (echo [错误] 未检测到 Python 3.11+ & pause & exit /b 1)

rem ---- 配置引导 ----
if not exist "config.env" (
    if exist "config.template.env" (
        echo [提示] 未找到 config.env，将从模板复制，请填入 API Key 后重跑
        copy /y "config.template.env" "config.env" >nul
        start notepad "config.env"
        pause
        exit /b 0
    )
)

rem ---- 环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 创建虚拟环境...
    python -m venv .venv
)
set "PY=.venv\Scripts\python.exe"

echo [2/4] 安装依赖（首次较慢）...
"%PY%" -m pip install -r requirements.txt -q --disable-pip-version-check

echo [3/4] 初始化数据库...
"%PY%" scripts/init_db.py

echo [4/4] 启动服务 (http://localhost:8080)...
start "" http://localhost:8080
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8080
