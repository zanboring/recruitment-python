@echo off
chcp 65001 >nul
rem ============================================================
rem  一键创建协作分支 dev-collab 并推送 GitHub
rem  用途：Trae 与 WorkBuddy 协作开发分支
rem  本地文件不会被改动，所有开发成果随分支走
rem ============================================================
cd /d "%~dp0"

echo [1/4] 检查当前分支...
for /f "delims=" %%i in ('git branch --show-current') do set "CUR=%%i"
echo   当前分支: %CUR%

echo [2/4] 创建/切换协作分支 dev-collab（基于当前工作区，文件不动）...
git checkout -b dev-collab 2>nul || git checkout dev-collab
if errorlevel 1 ( echo 分支切换失败 & pause & exit /b 1 )

echo [3/4] 暂存全部开发成果（dist/build/config.env 已被 .gitignore 排除）...
git add -A
if errorlevel 1 ( echo 暂存失败 & pause & exit /b 1 )

echo [4/4] 提交并推送 dev-collab 到 GitHub...
git commit -m "feat: 通用版软件化+内测交付准备（dev-collab 协作分支基线）

- 通用版：recsys.spec + build-exe.bat + run_entry.py + config.template.env + start-generic.bat
- config.env 优先级实现、前端资源多路径解析、SPA 单进程伺服
- 624 项 pytest 全绿、RecSys.exe 打包验证通过（12.8MB）
- 新增交接文档 COLLAB_HANDOFF.md（Trae <-> WorkBuddy 协作）"

if errorlevel 1 ( echo 提交失败，请检查 & pause & exit /b 1 )

git push -u origin dev-collab
if errorlevel 1 (
    echo 推送失败：请确认 GitHub 凭据已配置（git credential manager）
    echo 也可手动执行：git push -u origin dev-collab
    pause
    exit /b 1
)

echo.
echo ============================================
echo  已推送: origin/dev-collab
echo  后续开发都在 dev-collab 上，完成后
echo  再合并回 main（python 会自动保留本地文件）
echo ============================================
pause