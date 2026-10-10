# 项目总览与职责分工（2026-10-10 定稿）

> 制定：开发负责人（本会话）　｜　拍板：用户　｜　执行：CodeBuddy / Trae
> 定位：本文件是**长期职责的权威依据**；具体任务进度见 `MISSION-phase2.md`。

---

## 一、项目全景

| 维度 | 现状 |
|---|---|
| **技术栈** | FastAPI 0.115（异步）+ SQLAlchemy 2.0 + Vue3/Vite + Playwright 爬虫 + 多供应商 LLM |
| **代码规模** | 后端 90 个 `.py`；**22 services / 13 routers / 9 models（8 张表）/ 10 crawlers** |
| **测试** | 54 个测试文件，**764 passed / 0 failed**（含「4 个后端全部启用 Redis」场景） |
| **数据层** | SQLite（默认）/ MySQL / PostgreSQL 三库可切；Redis 可选，**连接失败自动降级** |
| **AI 链路** | 云端 DeepSeek / 智谱 → 本地 Ollama → 规则引擎**三级降级** + 熔断器保护 |
| **采集通道** | Playwright 爬虫（2 个平台）+ 浏览器油猴通道（4 个站点） |
| **后端分层** | `middleware/` → `routers/` → `dependencies.py` → `services/` → `models/` + `database.py` |
| **环境** | Redis ✅ 已装（`G:\Work\redis`，**需手动启动**）｜ Ollama ✅ 运行中 ｜ **Docker ❌ 未安装** |
| **分支** | `main` = `8988e65`，**已合并全部工作**，领先远端 25 个提交（待用户 push） |

---

## 二、架构硬化任务完成度

### v1（`MISSION-redis-docker.md`）

| 项 | 状态 |
|---|---|
| P0-1 ~ P0-6 | ✅ 全部完成 |
| P1-1 ~ P1-5 | ✅ 全部完成 |

### v2（`MISSION-phase2.md`）

| 项 | 内容 | 状态 |
|---|---|---|
| W1 | 测试隔离（解除 F3） | ✅ 已验收 |
| W2 | 爬虫日配额 + 域名节流走 Redis（A4） | ✅ 已验收 |
| W3 | AI 会话外置 Redis（A9） | ✅ 已验收 |
| W4 | Redis 接入运行时配置 | ✅ **4/4 后端已启用** |
| W5 | 装 Docker → A7/A8 | ⏳ **待用户** |
| W6 | 悬空文件处置 | ✅ 已完成（快照分支） |
| W7 | 合并 main 并发布 | 🟡 已合并；**待 push** |
| W8/W9 | 修复 flaky + 同类排查 | ✅ 已验收 |
| F5 | `session_backend` 隔离缺口 | ✅ 已验收 |

### 验收状态（A1-A11）

`A1 ✅｜A2 ✅｜A3 ✅｜A4 ✅｜A5 ✅｜A6 ✅｜A7 ⏳(Docker)｜A8 ⏳(Docker)｜A9 ✅｜A10 ✅｜A11 ✅`

> 仅剩 **A7/A8** 因 Docker 未安装而无法执行 —— 这是本项目收口的**唯一环境缺口**。

---

## 三、职能分工（各司其职）

> 原则：**一套代码 + 两个交付目标，按模块分人**。任何人不得越界改他人独占文件。

### CodeBuddy —— 后端架构与工程质量

| 域 | 独占文件 / 目录 |
|---|---|
| 中间件与限流 | `app/middleware/**` |
| 韧性组件 | `app/utils/circuit_breaker.py` |
| 部署与运维 | `docker-compose.yml`、`docker/**`、`.dockerignore`、`.env.docker.example` |
| 工程化闸门 | `tests/conftest.py`（隔离清单）、`tests/test_conftest_isolation.py` |
| 采集配额 | `app/crawlers/throttle.py`（**由负责人授权，原属采集域**） |

**长期职责**：Redis 化改造的延续、测试隔离机制的维护、CI/部署配置、跨副本一致性验证。
**特点**：提交纪律好（两次洁净提交，未误带他人改动）。

### Trae —— 数据导入、稳定链路与发布

| 域 | 独占文件 / 目录 |
|---|---|
| 数据导入（视觉/链接/CSV） | `app/services/url_import_service.py`、`vision_service.py`、`app/routers/jobs.py` |
| 稳定链路 | `app/services/llm_client.py`、`ai_service.py`、`job_checker.py` |
| 打包与发布 | `recsys.spec`、`build-exe.bat`、`run_entry.py`、`start*.bat` |
| 设置中心 | `app/routers/settings.py`、`frontend/.../Settings.vue` |
| 协作状态表 | `COLLAB.md` §2 |

**长期职责**：数据源接入、LLM 链路与降级、exe 打包与 Release、设置中心。
**特点**：擅长并发安全操作（用独立临时索引提交，避免动共享工作树）。

### 负责人（本会话）—— 方案、验收与协作

| 域 | 独占文件 / 目录 |
|---|---|
| 任务书与职责 | `collab/MISSION-*.md`、`collab/ROLES.md`、`collab/README.md` |
| 协作信道 | `collab/inbox-codebuddy.md`、`collab/inbox-trae.md` |
| 交接与说明 | `HANDOFF.md`、验收记录 |

**长期职责**：需求拆解与派工、**独立验收**（双进程 + 对照组）、分支与合并、汇总汇报用户。
**不写**：业务代码（由两个实施方写），避免"既当运动员又当裁判"。

### 用户 —— 拍板与环境

| 事项 | 说明 |
|---|---|
| 环境供给 | 装 Docker、Redis 开机自启方式 |
| 发布 | `git push`（AI 沙箱网络受限，必须由用户执行） |
| 决策 | 合入 main 的时机、任务优先级变更 |

---

## 四、协作铁律（三方共同遵守）

1. **分支纪律**：CodeBuddy → `dev-codebuddy`；Trae → `dev-trae`；负责人 → `dev-workbuddy`。
2. **提交前 `git rev-parse --abbrev-ref HEAD`；提交后 `git log --oneline -1` 复核**。
3. **任何超过 1 分钟的操作（尤其全量测试）前后，都要重新确认分支**（已发生两次分支错位）。
4. **禁止 `git add -A`** —— 逐个列出自己的文件。
5. **一个文件一个写者**（`collab/` 下的每个信道文件都有唯一写者）。
6. 改他人独占文件前，**先在直通信道说一声**。
7. 发现对方代码有问题 → 写一条消息，**必须带可复现步骤**。

---

## 五、当前待办（按优先级）

| # | 事项 | 归属 | 依赖 |
|---|---|---|---|
| 1 | `git push origin main` | **用户** | 有网络的终端 |
| 2 | 装 Docker Desktop → 验收 **A7/A8** | **用户** | Disk image 指到 `G:\Work\docker` |
| 3 | Redis 开机自启（计划任务 / 启动文件夹） | **用户 + 负责人** | 需定方式 |
| 4 | 从 `main` 出发布包（面试总版 + 兼容版） | **Trae** | 需先完成 1 |
| 5 | 兼容版 exe 的干净目录启动验证 | **Trae** | 需先完成 4 |

> ⚠️ **给用户的命令写法**：本机终端是 **Windows PowerShell 5.1**，**不支持 `&&` 连接符**
> （`&&` 是 PowerShell 7+ 语法，5.1 会报「标记"&&"不是此版本中的有效语句分隔符」）。
> 请用**分行**或**分号**：
> ```powershell
> cd D:\Dev\recruitment-python
> git push origin main
> ```
> 或 `cd D:\Dev\recruitment-python; git push origin main`
