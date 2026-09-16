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

rem ---- 3. 执行打包 ----
".venv\Scripts\python.exe" -m PyInstaller recsys.spec --noconfirm

echo.
echo ============================================
echo   打包完成：dist\RecSys\RecSys.exe
echo   使用：复制 dist\RecSys 整个目录到目标电脑，
echo        在目录里放 config.env（见 config.template.env）填入 API Key
echo        双击 RecSys.exe 启动，访问 http://localhost:8080
echo ============================================
pause
