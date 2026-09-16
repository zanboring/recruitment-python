# COLLAB.md — Trae ↔ WorkBuddy 协作中枢（唯一交流窗口）

> **这个文件是 Trae 与 WorkBuddy 的唯一交流窗口**。两边都只通过读写本文件协作，
> 完成一步就更新「任务状态表」，绝不绕开本文件私聊。用户反馈也记在「用户反馈区」。

---

## 1. 双版本规划（当前架构）

| 版本 | 形态 | 谁用 | 入口 |
|---|---|---|---|
| **面试总版** | 源码 + start.bat（全功能，含爬虫/视觉/链接/日报） | 开发者本人面试演示 | `start.bat` → http://localhost:8080 |
| **兼容版（通用版）** | PyInstaller 绿色软件 exe，零依赖，设置栏填 API Key 即用 | 其他用户下载 | `dist/RecSys/RecSys.exe`（打包：`build-exe.bat`） |

**兼容版核心机制（已就绪并验证）**：
- `recsys.spec` + `run_entry.py` + `build-exe.bat`：一键打包，前端打进 exe（约 12.8MB）
- `config.template.env` 配置模板；用户复制为 `config.env` 放 exe 同目录
- **设置栏填 Key**（新增，已端到端验证）：前端设置页写 API Key → 存 config.env → 热生效，换电脑复制目录即用
- 配置优先级已修复：config.env > .env，设置栏保存重启后必生效

---

## 2. 任务状态表（协作核心）

> 规则：当前接手方做完 → 把「任务」「状态」「下一步」更新为下一位可执行的状态，
> **同时把 4.1 手动步骤提示给用户**，用户双击同步后下一位接手。

| # | 任务 | 负责方 | 状态 | 说明 / 下一步 |
|---|---|---|---|---|
| 1 | 新增爬虫平台（前程无忧，独立文件不碰 base.py） | WorkBuddy | ✅ 已完成 | app/crawlers/job51.py（JSON 接口），commit b4db9df |
| 2 | 自动化日报增强（Excel 汇总 Sheet / Webhook 推送 / 定时可配置） | WorkBuddy | ✅ 已完成 | 透视表+Webhook，commit 4e3f98e |
| 3 | 链接导入 HTTP 降级路径（exe 无 Playwright 也能导入静态详情页） | Trae | ✅ 已完成 | url_import_service.py + 4 个新测试全绿 |
| 4 | 设置栏填 API Key（写 config.env 热生效 + 脱敏 + 白名单） | Trae | ✅ 已完成 | settings.py + Settings.vue，端到端验证通过 |
| 5 | 岗位存活核查 / 缓存 / 熔断稳定链路 | Trae | 在 dev-trae 维护 | 已有完整实现，按需增强 |
| 6 | 兼容版打包发布（内测版 v0.1 Release） | 待定 | 暂缓 | zip 已备好 27.2MB，等用户确认发布方式 |
| 7 | 兼容版启动自动建表 + 版本发布机制 | WorkBuddy | ✅ 已完成 | de93ddf / d38e63c |

### 任务 1 详情（WorkBuddy）
- 新文件 `app/crawlers/51job.py`（或 zhaopin/liepin），**不修改** base.py / boss.py / cleaner.py
- 复用 base.py 反爬护栏；字段对齐 `app/models/job.py`
- 验收：独立单测 + `pytest tests/test_anticrawl.py` 保持全绿

### 任务 2 详情（WorkBuddy）
- 只动 report_service.py / report.py / scheduler.py / config.py / config.template.env / Report.vue
- Webhook/邮件为可选项，无 key 静默跳过；定时移入配置
- 验收：`pytest tests/test_report_service.py` 全绿

---

## 3. 用户反馈区（用户 ↔ 协作组）

**格式**：`[日期] 反馈内容 → [接手方] 处理结果`
**规则**：用户反馈写在这里；接手方处理完在「任务状态表」更新并标记此处。

- （暂无 — 用户尚未开始闭门造车实测）

---

## 4. 协作工作流（全自动接力，唯一手动步骤是用户双击脚本）

### 4.1 每次同步（用户执行，约 5 秒）
```
项目根目录双击 git-sync.cmd → 自动：fetch → 当前分支 pull → add → commit → push
```
> OneDrive 权限限制：git 写操作必须用户手动跑，AI 无法代跑。这是唯一手动步骤。

### 4.2 接手方工作流（AI）
1. 读本文件顶部「任务状态表」，找状态=待开发/待接过 且负责方=自己的任务
2. 完成 → 更新状态 ✅ + 写下一位的「下一步」+ 更新反馈区
3. 提醒用户双击 git-sync.cmd 同步

### 4.3 分支约定
- **WorkBuddy 在 `dev-workbuddy` 分支**；**Trae 在 `dev-trae` 分支**
- 禁止直接改 main；不碰对方负责的文件（见任务表）
- 完成后由用户统一合并回 main

### 4.4 验证约定
- 后端：`python -m pytest -q`（项目 .venv）
- 前端改动：`cd frontend && npm run build`
- 通用版改动：`build-exe.bat` 重打验证

---

## 5. 环境备忘
- 本机 venv：`.venv\Scripts\python.exe`（已装 PyInstaller）
- 前端产物：`frontend\dist` 已存在
- git 凭据：zanboring / 1220650824@qq.com；远端 origin 已配
- git 写操作被 OneDrive 权限拦截 → 必须用户双击脚本（见 4.1）
- 测试坑：全量测试有 1 项 `test_chat_falls_back_to_local_engine` 因本机配置真实 DEEPSEEK key 而失败，属环境依赖非代码问题