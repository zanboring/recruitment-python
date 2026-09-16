# 开发协作交接文档（Trae ↔ WorkBuddy）

> 本文档用于在 **Trae** 与 **WorkBuddy** 两个 AI 编程助手之间交接。二者各持一条分支并行开发，最终合并回 `main`。请先读完整文档再动手。
>
> **任务分工、文件级边界与协作纪律见 [`COLLAB_TASKS.md`](./COLLAB_TASKS.md)。**

---

## 1. 项目是什么

**AI 招聘系统**（Python 重构版，原 Java 版前端 Vue3 保留复用）。

- 代码仓库：`github.com/zanboring/recruitment-python`
- 默认分支：`main`（稳定，只接受验收通过的合并）
- `dev-workbuddy`：**WorkBuddy** 的开发分支
- `dev-trae`：**Trae** 的开发分支
- ⚠️ 本文档早期版本提到的协作分支 `dev-collab` **从未被创建**，该名称已废弃，请勿再建。
  分支脚本以 `git-trae-branch.cmd` / `git-merge-main.cmd` 为准（`git-collab.cmd` 已废弃）。
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

1. **只在自己的分支上开发**（WorkBuddy → `dev-workbuddy`，Trae → `dev-trae`），禁止直接改 `main`。
2. 改动尽量小步、独立 commit，message 用中文规范（feat/fix/docs/refactor）。
3. 动手前先看本交接文档 + `COLLAB_TASKS.md` + README + TECH_SPEC.md，避免重复实现。
4. 改完必须验证：
   - 后端：`python -m pytest -q`（项目 .venv，当前基线 **466 项全绿**）
   - 前端（改过前端时）：`cd frontend && npm run build`
   - 通用版相关改动：重跑 `build-exe.bat` 验证打包
5. **两个助手不能同时操作本目录的 git。**
   本目录只有一个工作区，三条分支共用。一方执行 `git checkout` / `git reset --hard` 时，
   另一方未提交的改动会被静默丢弃（已实际发生一次）。改完立刻 commit，切分支前先 `git status`。
6. 冲突处理：改动同一文件时，先 `git pull --rebase origin <自己的分支>` 再合并。
7. **禁止 `git add -A` 盲提**：切分支后工作区文件不跟随分支，盲提会把 `frontend/node_modules/`
   （16764 个文件）和 12.7MB 的 `RecSys.exe` 一起提交。提交前先 `git status` 核对。

## 5. 当前进行中 / 待办

- [x] 代码推送到 GitHub —— `main` / `dev-workbuddy` / `dev-trae` 三条分支均已就绪
- [x] 测试 hermetic 修复（`a5619bc`）：conftest 未清 `deepseek_api_key`，本机配了 key 时
      `test_chat_falls_back_to_local_engine` 等用例失败，修复后 466 项全绿
- [ ] 功能开发按 `COLLAB_TASKS.md` 的分工进行（WorkBuddy: B+D，Trae: A+C）
- [ ] 发布 GitHub Release 内测版 v0.1（上传 `dist\RecSys` 的 zip）
  - 注意：本机未装 `gh` CLI，GitHub MCP 也无 create-release 能力 → 需用户网页手动上传，或先 `winget install GitHub.cli`
- [ ] 内测反馈 → 迭代更新流程：改代码 → `build-exe.bat` → 新 tag 新 Release

## 6. 环境备忘

- 本机 venv：`.venv\Scripts\python.exe`（Python 3.11.9，已装 PyInstaller）
- 前端构建：`frontend\dist` 已存在（npm run build 产物）
- git 凭据：zanboring / 1220650824@qq.com，远端 origin 已配置
- 坑：`.git` 在 OneDrive 下。**实测 AI 沙箱对 `.git` 有写权限**（本节原先写的「无写权限」已过期），
  但**推送 GitHub 会失败**（沙箱内 `git fetch` 报 `schannel: server closed abruptly`）——
  所以本地提交由 AI 完成、`git push` 由用户执行。
