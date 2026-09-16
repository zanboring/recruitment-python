@echo off
rem ============================================================
rem  COLLAB 一键同步：拉取最新 + 提交本地 + 推送
rem  用法：每次 Trae/WorkBuddy 更新后，双击本文件即可接力
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

for /f "delims=" %%i in ('git branch --show-current') do set "CUR=%%i"
echo [1/4] 当前分支: %CUR%

echo [2/4] 拉取远端（merge 到当前分支）...
git pull origin %CUR% --no-rebase
if errorlevel 1 (
    echo [警告] 拉取冲突或失败，请勿覆盖对方成果！
    echo 处理：人工解决冲突后再回到本脚本
    pause
    exit /b 1
)

echo [3/4] 暂存并提交本地改动...
git add -A
set "MSG=collab sync [%CUR%]"
git commit -m "%MSG%" 2>nul
if errorlevel 1 (
    echo [提示] 无新改动，跳过提交（正常）
)

echo [4/4] 推送...
git push origin %CUR%
if errorlevel 1 ( echo [错误] 推送失败，检查网络/凭据 & pause & exit /b 1 )

echo.
echo ============================================
echo  同步完成！分支 %CUR% 已和远端一致
echo  下一次接力：对方 pull 到最新后继续干活
echo ============================================
pause
