@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  协作状态检查（WorkBuddy / Trae 两个工作区通用）
rem  双击即可，无需参数。输出：分支位置 / 对方新提交 /
rem  合并冲突预警 / 收件箱是否有新消息
rem ============================================================

rem 找一个可用的 Python：优先本工作区 .venv，其次桌面总版的 .venv，最后 PATH
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\OneDrive - Ormesby Primary\Desktop\recruitment-python\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "collab\check.py"
if errorlevel 1 (
    echo.
    echo [提示] 检查脚本未正常结束。若提示找不到 Python，请确认项目 .venv 存在，
    echo        或把本文件放在项目根目录下运行。
)

echo.
pause
