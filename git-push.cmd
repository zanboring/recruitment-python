@echo off
rem ============================================================
rem  一键提交并推送到 GitHub（108 个文件已暂存，直接 commit+push）
rem  用法：双击本文件
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo 正在提交（108 个文件已暂存）...
git commit -m "feat: 系统全面升级——数据多源化、技能画像、自动化日报、软件化一键启动

- 数据多源化：视觉识别导入（GLM-4V 截图到岗位）、链接导入（Playwright 读页面）、
  爬虫保存岗位详情 URL、CSV 批量导入、种子数据脚本（幂等）
- 技能画像：输入技能，逐岗位覆盖率/缺口技能/市场热门技能排行
- 自动化日报：每日聚合统计，Excel 报表，AI 摘要（三级降级），前端页面
- 岗位存活核查：后台慢速扫库核对已存 URL 岗位是否下线（限速错峰/异常隔离）
- 稳定性：Redis 缓存（失败降级内存）、熔断器（LLM 防级联雪崩）、PostgreSQL 多库
- 反爬增强：完整浏览器请求头、风控信号检测、自适应降速、代理池配置
- 软件化：start.bat 单进程全栈一键启动、Vue 产物由后端伺服（SPA 路由回退）
- 测试：410 到 466 项全绿；Docker Compose 全栈部署"

if errorlevel 1 (
    echo 提交失败，请检查错误信息
    pause
    exit /b 1
)

echo 提交成功，正在推送 origin/main ...
git push origin main

if errorlevel 1 (
    echo 推送失败：请确认 GitHub 凭据已配置（git credential manager）
    echo 也可手动执行：git push origin main
    pause
    exit /b 1
)

echo.
echo ============================================
echo  已推送到 GitHub: github.com/zanboring/recruitment-python
echo ============================================
pause
