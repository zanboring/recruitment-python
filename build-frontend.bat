@echo off
chcp 65001 >nul
cd /d "%~dp0frontend"
echo 构建前端（需 Node.js 20+）...
call npm install --no-fund --no-audit
call npm run build
echo 构建完成，产物在 frontend/dist/
pause
