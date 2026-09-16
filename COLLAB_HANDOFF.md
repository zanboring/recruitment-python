# 开发协作交接文档（Trae ↔ WorkBuddy）

> 本文档用于在 **Trae** 与 **WorkBuddy** 两个 AI 编程助手之间交接，二者在协作分支 `dev-collab` 上并行/接力开发，最终合并回 `main`。请先读完整文档再动手。

---

## 1. 项目是什么

**AI 招聘系统**（Python 重构版，原 Java 版前端 Vue3 保留复用）。

- 代码仓库：`github.com/zanboring/recruitment-python`
- 默认分支：`main`（稳定，只接受来自 dev-collab 的合并）
- 协作分支：`dev-collab`（**当前所有开发都在这里**）
- 本地路径：`C:\Users\zanboring\OneDrive - Ormesby Primary\Desktop\recruitment-python`

## 2. 两个交付版本（重要）

| 版本 | 入口 | 用途 | 依赖 |
|---|---|---|---|
| 本地调试版 | `start.bat` + 项目源码 | 开发者本机调试（含爬虫、playwright） | Python 3.11+ |
| 通用版（软件） | `dist\RecSys\RecSys.exe` | 任意电脑绿色软件，设置里填 API Key 即用 | 零依赖（SQLite 内置） |

**通用版发布机制**（已全部就绪并验证）：
- `recsys.spec` PyInstaller 配置，前端打进 exe；`run_entry.py` 是打包入口
- `build-exe.bat` 一键打包 → 产出 `dist\RecSys\RecSys.exe`（约 12.8 MB，已成功打包验证）
- `config.template.env` 是配置模板（用户复制为 `config.env` 放 exe 同目录，填 `DEEPSEEK_API_KEY` / `ZHIPUAI_API_KEY`）
- `config.env` 优先级高于 `.env`（`app/config.py` 已实现），换电脑只需拷目录 + 填 key
- `.gitignore` 已排除 `build/`、`dist/`、`frontend/dist/`、`config.env`

## 3. 已完成功能（勿重复开发）

- 数据多源化：视觉识别导入（GLM-4V）、链接导入（Playwright+LLM）、爬虫保存详情 URL、CSV 批量导入、种子数据脚本（幂等）
- 技能画像：输入技能 → 逐岗位覆盖率/缺口/市场热门排行
- 自动化日报：每日聚合 + Excel 报表 + AI 摘要（三级降级），前端页面
- 岗位存活核查：后台慢速扫库核对已存 URL 是否下线（限速错峰/异常隔离），启动不阻塞
- 稳定性：Redis 缓存（失败降级内存）、LLM 熔断器、PostgreSQL 多库支持
- 反爬：完整浏览器请求头、风控信号检测、自适应降速、代理池配置
- 前端：Vue3 全量页面，`npm run build` 产物由后端单进程伺服（SPA 路由回退）
- 测试：pytest 466 项全绿

## 4. 协作约定（必须遵守）

1. **在 `dev-collab` 分支上开发**，禁止直接改 `main`。
2. 改动尽量小步、独立 commit，message 用中文规范（feat/fix/docs/refactor）。
3. 动手前先看本交接文档 + README + TECH_SPEC.md，避免重复实现。
4. 改完必须验证：
   - 后端：`python -m pytest -q`（项目 .venv）
   - 前端（改过前端时）：`cd frontend && npm run build`
   - 通用版相关改动：重跑 `build-exe.bat` 验证打包
5. 冲突处理：多人同时改同一文件时，先 `git pull --rebase origin dev-collab` 再合并。

## 5. 当前进行中 / 待办

- [ ] 代码推送到 GitHub（本地已完成 staged，需用户手动双击脚本，见 `git-collab.cmd`）
- [ ] 发布 GitHub Release 内测版 v0.1（上传 `dist\RecSys` 的 zip）
  - 注意：本机未装 `gh` CLI，GitHub MCP 也无 create-release 能力 → 需用户网页手动上传，或先 `winget install GitHub.cli`
- [ ] 内测反馈 → 迭代更新流程：改代码 → `build-exe.bat` → 新 tag 新 Release

## 6. 环境备忘

- 本机 venv：`.venv\Scripts\python.exe`（已装 PyInstaller）
- 前端构建：`frontend\dist` 已存在（npm run build 产物）
- git 凭据：zanboring / 1220650824@qq.com，远端 origin 已配置
- 坑：.git 目录在 OneDrive 下，AI 沙箱无写权限 → **git 写操作必须由用户双击脚本完成**（如 `git-collab.cmd`），AI 只改工作区文件
