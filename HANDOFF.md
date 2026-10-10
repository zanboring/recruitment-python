# 工作区交接文档

> **交接人**：WorkBuddy（本会话）
> **接手人**：新工作空间的智能体
> **生成时间**：2026-10-10 14:30
> **项目唯一工作目录**：`D:\Dev\recruitment-python`

---

## 0. 三十秒速读（先看这段，其余按需查）

| 项 | 现状 |
|---|---|
| **唯一工作目录** | `D:\Dev\recruitment-python` —— **只在这里读、写、commit** |
| **禁止使用的目录** | 桌面那份（已搬空，仅剩 1 个残留文件）、`H:\Backup\*` 三份旧副本 |
| **当前分支** | `dev-codebuddy` @ `d31eee5`（CodeBuddy 的实现分支，工作树就停在这里） |
| **`main`** | `4cae06d`，**全程未被污染**，等用户拍板后合并 |
| **任务进度** | P0 完成 5/6，P1 完成 4/5 —— 只剩 `P0-2`（爬虫配额，归 WorkBuddy 域）与 `P1-1`（AI 会话，等 Trae 授权） |
| **测试基线** | CodeBuddy 自报 **736 passed / 0 failed**（起点 697）。⚠️ 我上次实测是 717，接手后**请自己再跑一遍确认** |
| **最大环境缺口** | **Docker 未安装** → 验收项 A7 / A8 无法执行 |
| **Redis** | ✅ 已部署在 `G:\Work\redis`，可启动（见 §5.2） |
| **Ollama** | ✅ 已装且运行中，模型与项目配置对齐 |

**接手第一天建议做的 5 件事**见 §12。

---

## 1. 项目是什么

**recruitment-python** —— 招聘数据采集与 AI 分析系统。一句话：把招聘网站的岗位数据采回来、洗干净入库，再做强分析 + AI 问答 + 自动化日报。

- **技术栈**：FastAPI 0.115（异步）+ SQLAlchemy 2.0 + Vue3/Vite + Playwright 爬虫 + 多供应商 LLM（DeepSeek / 智谱 / 本地 Ollama 三级降级）
- **后端分层**：`middleware/` → `routers/` → `dependencies.py` → `services/` → `models/` + `database.py`
- **规模**：8 张表、21 个 service、11 个 router、3 个 recommender、3 个 evaluation 模块
- **入口**：`start.bat`（源码模式单进程全栈）→ http://localhost:8080
- **更多背景**：`README.md`、`TECH_SPEC.md`、`COLLAB.md`、`INTERVIEW.md`

---

## 2. 目录与禁止事项（**先记这条，最容易踩**）

### ✅ 唯一工作目录

```
D:\Dev\recruitment-python
```

判定特征（两个都要满足）：
1. 存在 `collab/MISSION-redis-docker.md`
2. `git branch` 里有 `dev-workbuddy` / `dev-codebuddy`

### ⛔ 禁止使用的目录

| 路径 | 状态 | 说明 |
|---|---|---|
| `C:\Users\Administrator\Desktop\recruitment-python` | ⚠️ **已搬空，仅剩 1 个残留文件** | 曾有完整项目副本，2026-10-10 已由 WorkBuddy 搬到 H 盘归档。**只剩 `.codebuddy\settings.local.json` 因被占用删不掉**，需用户手工删除整个空壳目录 |
| `H:\Backup\Desktop-recruitment-python-已废弃-20261010` | 归档（勿编辑） | 上述桌面副本的归档，含 `.git` / `.venv` / `node_modules`。**它是一份带独立 `.git` 且远端指向同一 GitHub 仓库的副本** —— 在上面 commit + push 会与本仓库历史打架 |
| `H:\Backup\Dev\recruitment-python`（09-28） | 旧备份，勿用 | |
| `H:\Backup\recruitment-python-workarea-20260928` | 旧备份，勿用 | |
| `H:\Backup\C_Users_20260924\Desktop\recruitment-python`（09-23） | 旧备份，勿用 | |

> ⚠️ **最阴的陷阱**：桌面那份的 `.venv` 实测**是能跑的**（`pyvenv.cfg` 的 `home` 指向系统 Python 绝对路径，与项目路径无关）。
> **「跑得起来」不等于「是活的」** —— 它停在 `4fe021c`，不含后续任何提交。

---

## 3. 当前任务：Redis + Docker 架构硬化

### 3.1 为什么做这个任务

用户目标：用这个项目**体现全面的 AI 全栈 + AI 应用开发能力**。因此判据不是「够不够用」，而是「架构是否完整、每个中间件是否真正承担职责」。

**核心立论（整个任务存在的理由）**：

> **Redis 的价值只在多副本下成立。**
> 项目原为单进程部署，所以限流计数、爬虫日配额、AI 会话历史、熔断器状态**全在进程内**。
> 扩到 2 个副本后它们会同时失效：限流配额变 2 倍（防爆破形同虚设）、
> 爬虫日配额变 2 倍（**对招聘站的合规承诺不成立**）、会话分片串台、熔断各自为战。
> **只加 Redis 不加副本 = 白加。**

### 3.2 任务书（权威来源）

📄 **`collab/MISSION-redis-docker.md`**（约 42 KB）

小节结构：

| 节 | 内容 |
|---|---|
| §0 | 目标与核心判断 |
| §1 | 现状核实（R1-R5 进程内实现清单 / D1-D6 Docker 缺口，均附文件行号） |
| §1.5 | **环境实况**（磁盘布局、工具链、Redis 部署信息、验收顺序） |
| §2 | 任务分解：P0 必做 / P1 加分 / 明确不做 |
| §3 | 硬性工程约束（4 条） |
| §4 | **验收标准 A1-A10** |
| §5 | 文件所有权与边界声明 |
| §5.5 | 分支约定 |
| §6 | 三方协作流程 |
| §7 | **交付记录**（CodeBuddy 写的 7 条 DELIVER） |
| §8 | **验收记录**（WorkBuddy 写的 ACCEPT） |

### 3.3 任务完成状态

| 任务 | 内容 | 状态 |
|---|---|---|
| P0-1 | Redis 滑动窗口限流（Lua）+ 内存降级 | ✅ 已交付并**已验收通过** |
| P0-2 | 爬虫全局日配额 + 域名节流走 Redis | ❌ **未做** —— 归 WorkBuddy 域，被 `config.py` 悬空改动搁置 |
| P0-3 | 多副本部署（`--workers 2`） | ✅ 已交付（待 A8 验收） |
| P0-4 | `.dockerignore` | ✅ 已交付 |
| P0-5 | healthcheck + `depends_on: service_healthy` | ✅ 已交付（待 A8 验收） |
| P0-6 | 部署配置模板 `.env.docker.example` | ✅ 已交付 |
| P1-1 | AI 会话外置 Redis | ❌ **未做** —— 需 Trae 授权（`ai_service.py` 属 Trae 域） |
| P1-2 | 熔断器状态 Redis 共享 | ✅ 已交付（待验收 A10） |
| P1-3 | nginx gzip + 静态缓存头 | ✅ 已交付 |
| P1-4 | 日志轮转 | ✅ 已交付 |
| P1-5 | 一键验收脚本 `scripts/verify_redis_docker.py` | ✅ 已交付 |

---

## 4. 分支与协作协议

### 4.1 分支

| 分支 | 位置 | 谁写 |
|---|---|---|
| `main` | `4cae06d` | **无人直接写**，等用户拍板后合并 |
| `dev-workbuddy` | `b7152c6` | WorkBuddy（文档/验收/采集域） |
| `dev-codebuddy` | `d31eee5` | CodeBuddy（实现）—— **工作树当前停在这里** |

> **用户明确要求：不许写 main。** 两个 AI 各写自己的分支。

### 4.2 协作信道（一个文件一个写者，结构上不可能冲突）

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `collab/MISSION-redis-docker.md` §7 交付记录 | CodeBuddy | 全体 |
| `collab/MISSION-redis-docker.md` §8 验收记录 | WorkBuddy | 全体 |
| `collab/inbox-codebuddy.md` | WorkBuddy | CodeBuddy |
| `collab/inbox-codebuddy-reply.md` | CodeBuddy | WorkBuddy |
| `collab/inbox-trae.md` | WorkBuddy | Trae |
| `collab/inbox-workbuddy.md` | Trae | WorkBuddy |
| `collab/README.md` | WorkBuddy | 全体 |

**规则**：只**追加**，每条以行首 `## MSG` 开头，**不修改历史行**（改历史行会造成冲突）。
通信频率：里程碑 / 发现 bug / 要动对方文件 / 阻塞 才写消息；日常小改不写。

### 4.3 文件所有权（`collab/README.md` §5，动手前必查）

| 归属 | 独占文件 / 目录 |
|---|---|
| **WorkBuddy** | `app/crawlers/**`、`app/services/crawler_service.py`、`app/routers/crawler.py`、`userscript/**`、`app/services/report_service.py`、`app/version.py`、`collab/**`（除 `inbox-workbuddy.md`） |
| **Trae** | `app/services/url_import_service.py`、`vision_service.py`、`app/routers/jobs.py`、`app/services/llm_client.py`、`ai_service.py`、`job_checker.py`、`recsys.spec`、`build-exe.bat`、`start*.bat`、`app/routers/settings.py` |
| **共用** | `app/config.py`（新增项**追加到 `Settings` 类末尾自己的区块**）、`app/main.py`（追加到末尾）、`README.md`（只改自己章节）、`tests/**`（只新增自己的文件） |
| **CodeBuddy（本轮新增）** | `docker-compose.yml`、`docker/**`、`.dockerignore`、`.env.docker.example`、`app/middleware/rate_limit.py`、`app/utils/circuit_breaker.py`（用子类覆写，未动父类） |

---

## 5. 环境实况

### 5.1 磁盘布局（2026-10-10 换盘完成，实测）

| 盘符 | 型号 | 接口 | 卷容量 | 可用 | 卷标 |
|---|---|---|---|---|---|
| **C** | WD_BLACK **SN850X 2000GB** | NVMe | 500 GB | 401.7 GB | Win11ProW X64 |
| **D** | 同上（**C、D 同一块盘**） | NVMe | 1362.7 GB | 905.2 GB | Game |
| **E** | WD_BLACK **SN7100 1TB** | NVMe | 931.5 GB | 617 GB | Game |
| **F** | `WDS100T1X0E-00AFY0` = **WD SN850 1TB** | NVMe | 501 GB | 337.3 GB | Game |
| **G** | 同上（**F、G 同一块盘**） | NVMe | 430.5 GB | 429.9 GB | Work |
| **H** | ST2000DM001（希捷 2TB） | SATA | 1863 GB | 1680.2 GB | Repository |
| **I** | Kingston DataTraveler 3.0（USB 优盘） | USB | 57.8 GB | 49.9 GB | BACKUP（exFAT） |

- 项目在 **D 盘 = 最快的盘**，位置合理。
- **`G:\Work\`** 是工作数据根：`cache/{npm,pip}`、`docker/`、`ollama/models`、`wsl/`、`projects/`。
- 缓存：`npm cache = D:\Dev\caches\npm`；`pip cache = G:\Work\cache\pip`。
- ⚠️ **`D:\` 根目录有 9 个隐藏 junction** 指向 `D:\Apps\*`（豆包/夸克/微信/QQ/CloudMusic/Trae CN/TRAE SOLO CN/cursor/图吧工具箱）。
  **`rm -rf` 或 `Remove-Item -Recurse` 会连带删掉真实程序**；真要删只能用 `rmdir "D:\豆包"`（对 junction 只删链接本身）。
- ⚠️ **`I:` 优盘上有约 6.5 GB 不可再生资料**（`study` 5.2GB、`项目源码压缩包` 805MB、`Python期末考核` 467MB），**只有这一份副本**。

### 5.2 Redis（✅ 已部署，可直接用）

| 项 | 值 |
|---|---|
| 版本 | **Redis 8.10.2**（免安装 Windows 版，`redis-windows/redis-windows` release 8.10.2 msys2） |
| 位置 | `G:\Work\redis`（不占 C 盘、不需管理员） |
| **启动方式** | 双击 **`G:\Work\redis\start-redis.cmd`**（窗口保持打开 = Redis 在运行，关闭即停） |
| 配置 | `G:\Work\redis\recsys-redis.conf`：AOF `appendonly yes`、`maxmemory 512mb` + `allkeys-lru`、仅监听 `127.0.0.1:6379` |
| 数据 / 日志 | `G:\Work\redis\data` / `G:\Work\redis\logs\redis.log` |
| 手动启动 | `cd /d G:\Work\redis && redis-server.exe recsys-redis.conf` |
| 连通性自检 | `G:\Work\redis\redis-cli.exe PING` → 期望 `PONG` |

**项目接入方式**（两个环境变量，**必须同时给**）：

```bash
RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
```

不设这两项时项目走内存实现，行为与改造前**完全一致**（已验收 A2）。

> ⚠️ **踩坑记录**：conf 里 `logfile` / `dir` **必须写绝对路径**。
> Redis 会把相对的 `logfile` 解析到 `dir` 之下，写 `./logs/redis.log` 会直接
> `FATAL CONFIG FILE ERROR: Can't open the log file`。

> ⚠️ **Redis 当前未随开机自启**（只有 `start-redis.cmd`）。若要「真正用上 Redis」而不靠人工点，
> **需要一个自启机制**（计划任务 / 启动文件夹快捷方式）。见 §7 待办 T3。

### 5.3 Ollama（✅ 已装且运行中）

| 项 | 值 |
|---|---|
| 路径 | `C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe` |
| 模型目录 | `OLLAMA_MODELS=G:\Work\ollama\models` |
| 已拉取模型 | `qwen3.5:4b`（3.4 GB）、`qwen3-embedding:0.6b`（639 MB） |
| 其他环境变量 | `OLLAMA_MAX_LOADED_MODELS=2`、`OLLAMA_NUM_PARALLEL=1`、`OLLAMA_CONTEXT_LENGTH=8192`、`OLLAMA_FLASH_ATTENTION=1` |

项目 `.env` 里的配置**与已拉取模型完全对齐**：
`OLLAMA_MODEL=qwen3.5:4b`、`OLLAMA_CODE_MODEL=qwen3.5:4b`、`OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b`、`OLLAMA_ENABLED=true`。

→ **「云端 DeepSeek/智谱 → 本地 Ollama → 规则引擎」三级降级链现在可以离线、零成本完整演示**，
这是 AI 应用能力展示里很有说服力的一环。

### 5.4 Docker（❌ **未安装** —— 当前最大的验收缺口）

实测：`docker` 不在 PATH；`C:\Program Files\Docker\Docker\Docker Desktop.exe` 不存在；无 `com.docker.service`。

**未安装的直接后果**：验收项 **A7 / A8 无法执行**，CodeBuddy 已交付的 P0-3/4/5 全部处于「代码已写完、无法验收」状态。

**安装要点**：
1. 从 https://www.docker.com/products/docker-desktop/ 下载 Windows 版安装包。
2. 安装 **需要管理员提权（UAC）**，且可能**需要重启**。
3. 需要 **WSL2** 或 Hyper-V 后端（用户已在 `G:\Work\wsl` 预留目录）。
4. ⚠️ **安装后务必把 `Disk image location` 指到 `G:\Work\docker`** —— 用户已在 G 盘预留该目录，
   默认放 C 盘会吃掉 C 盘仅剩的 401 GB（用户的底线诉求是「C 盘防满」）。

### 5.5 Python / 前端环境

- `.venv` = **本机 Python 3.14.7**，可用（`D:\Dev\recruitment-python\.venv\Scripts\python.exe`）
- `frontend/dist` ✅ 已构建（38 个文件）；`frontend/node_modules` ✅ 已安装
- `data/recsys.db` ✅ 存在（SQLite）
- 全量测试入口：`.venv/Scripts/python.exe -m pytest -q --tb=line -p no:warnings`

---

## 6. 已完成 & 已验收（带 commit）

| commit | 内容 | 交付方 | 验收状态 |
|---|---|---|---|
| `4bdfa97` | 下达任务书 + 建立 CodeBuddy 协作信道 | WorkBuddy | — |
| `8bec244` | 分支约定（main 停写） | WorkBuddy | — |
| `9cb2789` | 任务书补环境实况 | WorkBuddy | — |
| `9b411d1` | **P0-1** Redis Lua 滑动窗口限流 + 内存降级 | CodeBuddy | ✅ **已验收通过**（A1/A2/A3/A5/A6，见 §8） |
| `8db828f` | P0-1 验收记录 | WorkBuddy | — |
| `4fe021c` | 上报 config.py 悬空改动阻塞 | WorkBuddy | — |
| `7771929` | **P0-3/4/5/6** 多副本 + healthcheck + `.dockerignore` + 部署模板 | CodeBuddy | ⏳ 待 A7/A8（需 Docker） |
| `a8ae5c6` | **P1-2/3/5** 熔断器 Redis 共享 + nginx gzip + 一键验收脚本 | CodeBuddy | ⏳ 待验收 A10 |
| `79720df` | 上报 JWT_SECRET 冲突 + 桌面快照告警 | WorkBuddy | — |
| `826e0a0` | 修 JWT_SECRET 阻塞 + 澄清 `env_file` 不参与插值 | CodeBuddy | ⏳ 待复核 |
| `d31eee5` | CodeBuddy 回信 | CodeBuddy | — |
| `b7152c6` | 统一工作目录为 D 盘 | WorkBuddy | — |

**测试基线演进**：697（任务开始）→ 717（P0-1 完成后我实测）→ CodeBuddy 报 **736**（含 P1-2/3 的新增用例）。

**CodeBuddy 已实现但尚未验收的要点（供接手人复用）**：
- `tests/test_circuit_breaker_redis.py`（19 项）内含 **A10 的对照实验**：内存版两实例互不影响 vs Redis 版「一熔俱熔」——验 A10 时可直接用它对齐口径。
- `scripts/verify_redis_docker.py`：一键验收脚本。**它有意识地把 A3/A4/A6/A9/A10 排除在自动执行之外，只打印手工命令** —— 理由是在脚本里自动跑这些极易做成「看起来通过」的假验收。这个设计判断值得保留。

---

## 7. 待办（明确归属）

| # | 待办 | 归属 | 依赖 / 阻塞 |
|---|---|---|---|
| **T1** | **P0-2**：爬虫日配额 + 域名节流走 Redis（`app/crawlers/throttle.py`） | WorkBuddy（独占域） | 需往 `app/config.py` 加 `QUOTA_BACKEND`。**不再被悬空改动阻塞**——CodeBuddy 已验证可用 `git apply --cached` 只暂存自己的 hunk（见 §9.1） |
| **T2** | 验收 **A7 / A8**（compose 与镜像、就绪依赖） | WorkBuddy | **必须先装 Docker** |
| **T3** | **让 Redis 真正被用上**：把 `RATE_LIMIT_BACKEND=redis` + `REDIS_URL` 写进 `config.env`，并加开机自启 | 未有归属 | 用户明确要求「redis 也想办法用上」 |
| **T4** | 验收 **A10**（熔断器跨副本） | WorkBuddy | Redis 已就绪，**可立即做**（用本机 2 个 uvicorn worker） |
| **T5** | **P1-1** AI 会话外置 Redis | CodeBuddy（待授权） | `app/services/ai_service.py` 属 **Trae** 域。`inbox-workbuddy.md` 最后一条仍是 09-17，**Trae 未回复**。三选一：Trae 自己改 / 授权 CodeBuddy / 不做 |
| **T6** | 清理 `app/config.py` 的悬空改动与其余 ~14 个文件 | **用户拍板** | 见 §9.1，两个 AI 均未擅自认领 |
| **T7** | 删除桌面残留空壳目录 | 用户（手工） | 只剩 `.codebuddy\settings.local.json` 被占用删不掉 |
| **T8** | 合并 `dev-*` → `main` 并发布 | 用户拍板 | 见 §10 |

---

## 8. 验收状态总表（A1-A10）

| # | 判据 | 状态 |
|---|---|---|
| A1 | 全量测试 0 failed 且 ≥697 | ✅ 已验（我实测 717；现 CodeBuddy 报 736，**建议复跑**） |
| A2 | 单机不回归（不设 `REDIS_URL` 行为一致） | ✅ 已验 |
| **A3** | **限流跨副本**：2 进程总放行 ≤ limit | ✅ **已验**：redis = 10（= limit）；memory 对照组 = 20（= 2×limit） |
| A4 | 爬虫日配额跨副本 | ⏸️ 待 P0-2（T1） |
| A5 | Redis 掉线可降级（无 5xx） | ✅ 已验：死端口下启动正常，`[401×10, 429×4]`，**0 个 5xx** |
| A6 | 缓存跨进程一致 | ✅ 已验：配 Redis 时键真实存在且跨进程可读；对照组读 `None` |
| A7 | `docker compose config` 通过 + `.dockerignore` 生效前后 context 对比 | ⏸️ 待 Docker（T2） |
| A8 | 前端首屏无 502 + healthcheck healthy | ⏸️ 待 Docker（T2） |
| A9 | （P1-1）会话跨副本 | ⏸️ 待 P1-1（T5） |
| A10 | （P1-2）熔断跨副本 | ⏸️ 待验收（T4，**可立即做**） |

**A3 / A4 / A6 是本任务的核心证据** —— 这三条拿不出，整个改造就还是「装了但没用」。

---

## 9. ⚠️ 已知坑与陷阱（**这一节最容易出事，务必读完**）

### 9.1 `app/config.py` 里压着 108 行悬空改动（**未解决**）

**现象**：`app/config.py` 同时承载三层内容：

| 层 | 规模 | 来源 | 状态 |
|---|---|---|---|
| CodeBuddy 的 Redis 配置项 | 13 行 | `9b411d1` | ✅ 已提交 |
| **历史会话的反爬护栏配置** | **108 行** | **不属于任何人的当前任务**（疑为 `CRAWL_DOMAIN_MIN_INTERVAL` / `CRAWL_DAILY_QUOTA_PER_PLATFORM` / `CRAWL_ROBOTS_CHECK_ENABLED` / `SCHEDULED_CRAWL_GAP_*`） | ⚠️ **悬空** |

**后果**：**跨分支的 `git checkout` 会被 git 拒绝**
（`error: Your local changes to the following files would be overwritten by checkout: app/config.py`）。
当前工作树停在 `dev-codebuddy`，想切回 `dev-workbuddy` 会失败。

此外工作树里还有约 14 个同类悬空文件（`app/crawlers/base.py`、`app/dependencies.py`、
`app/services/llm_client.py`、`app/utils/circuit_breaker.py`、`userscript/recsys-collector.user.js` 等），
加上 19 个未跟踪文件（`reports/*.html` 审计报告、`ENV_MISSION_*` 等）——**共 33 个未提交条目**。

**处置纪律**：
- ❌ **严禁 `git add -A` / `git add .`** —— 会把他人的半成品一起卷进提交，之后无法分离
- ✅ 提交时**逐个列出自己的文件路径**
- ✅ 要在 `app/config.py` 加内容时，用 **`git apply --cached` 只暂存自己的 hunk**（CodeBuddy 验证过的做法）：

```bash
# 1) 生成整文件 diff，按独有关键字挑出自己的 hunk，拼成补丁
# 2) git apply --cached _my.patch     ← 只进索引，不动工作树
# 3) git diff --cached --stat         ← 复核：只应有自己的行数
```

  比 `git add -p` 可靠（后者在非交互环境难用，且相邻 hunk 会被合并成一块）。

- ✅ 新配置项的**落点**：`Settings` 类**末尾、CodeBuddy 区块之后**，新建自己的区块
  （CodeBuddy 的区块标记是 `# ===== Redis + Docker 架构硬化（CodeBuddy 区块） =====`）。
  那个位置距文件末尾的悬空改动有足够间隔，hunk 天然分开。

- **这 108 行的归属由用户拍板**（T6），两个 AI 均未擅自认领。

### 9.2 `JWT_SECRET` 与 compose 插值（**已修，但机理要懂**）

**曾经的坑**：`docker-compose.yml` 用 `${JWT_SECRET:?未设置...}`（`:?` 语义 = 未设置**或为空**都报错），
而项目根 `.env` 的 `JWT_SECRET` 是**空值**（真值在 `config.env`，长度 64）→ **`docker compose up` 启动前就失败**。

**已修**（`826e0a0`）：把 `JWT_SECRET` 补进项目根 `.env`（长度 64，与 `config.env` 同值）。

**⚠️ 一个被纠正的错误判断（我提的方案 A 是错的，记下来避免重犯）**：

> 我曾建议「给 backend 加 `env_file: [config.env]`」。**这是错的** —— 两者时机不同：
>
> | 机制 | 生效时机 | 作用对象 |
> |---|---|---|
> | `${VAR}` / `${VAR:?}` 插值 | compose **解析文件时** | compose 文件自身 |
> | service 级 `env_file:` | **创建容器时** | 容器内的环境变量 |
>
> `env_file:` 只注入容器环境，**不参与 compose 文件自身的插值** —— 加了之后 `${JWT_SECRET:?}`
> 依然会在解析阶段失败。**这类「看起来修了、实际仍报错」的改法比不改更危险。**
>
> **真正能复用 `config.env` 的方式是改插值来源**：
> ```bash
> docker compose --env-file config.env up -d --build
> ```
> （compose 顶部注释里已写明这个开关。）

### 9.3 验收方法学：**对照实验不可省**

A3 之所以可信，是因为同时跑了**对照组**：

| 后端 | 进程数 | 总放行 | 期望 |
|---|---|---|---|
| `RATE_LIMIT_BACKEND=redis` | 2 | **10** | == limit |
| `RATE_LIMIT_BACKEND=memory` | 2 | **20** | == 2 × limit |

**只有 redis 侧通过、memory 侧也翻倍，才能证明这个测试不恒真。**
凡涉及「跨副本」的验收项（A3/A4/A6/A9/A10），**必须先想清楚对照组是什么**，
否则会做成「看起来通过」的假验收。

### 9.4 本机工具链的坑（实测）

| 坑 | 说明 |
|---|---|
| **PowerShell 工具不回传 stdout** | 需 `Out-File` 写文件再 `Read`。用 `-Encoding ascii` + 纯英文标签最省事（`utf8` 写出的文件会被判定为 binary） |
| **bash 里调 `powershell.exe` 被安全策略拒** | 必须用 PowerShell 工具本身 |
| **同一条 PowerShell 命令里出现黑名单程序会整条作废** | 已知黑名单：`wsl.exe`、`sc.exe`、`reg.exe`、破坏性 cmdlet 名字面量（`Clear-Disk`/`Format-Volume`） |
| **删除文件会走回收站（且有过滤驱动层）** | 所有通道（`rm -rf` / `.NET File.Delete` / 内核 API）实测**都是进回收站**，空间零释放；要真释放需清空回收站 |
| **无 COM 实例化** | `New-Object -ComObject` 被拦（存在被占用的文件时无法程序化强制删除） |
| **`Add-Type` 被拦** | 无法编译 .NET 代码 / `SendToRecycleBin` 之类 |

### 9.5 启动后端时的两个小坑

1. **`/api/system/version` 需要鉴权**，返回 **401**。用它做「服务是否就绪」的探测会误判
   （`urllib` 对 401 抛 HTTPError，会被当成"服务没起来"）。
   **探测就绪请用 `/` 或 `/openapi.json`**（CodeBuddy 的 healthcheck 就正确用了 `/openapi.json`）。
2. **`OLLAMA_ENABLED`**：现在 Ollama 已装且模型对齐，**不要**再盲目设为 `false`；
   反过来若 Ollama 没运行，请求会先超时再降级，会拖慢首个 AI 请求。

---

## 10. 待用户拍板的事项

| # | 事项 | 选项 |
|---|---|---|
| D1 | **T6**：`app/config.py` 那 108 行悬空改动 + 其余 ~14 个悬空文件怎么处置 | a) `git add -p` 单独提交成「历史遗留配置」commit；b) 还原；c) 暂不处理（代价：跨分支 checkout 持续被拒） |
| D2 | **T7**：桌面残留空壳目录 | 手工删除（只剩 1 个被占用的文件，程序删不掉） |
| D3 | **装 Docker Desktop** | 决定 T2（A7/A8）能否开工。注意 UAC + 可能需重启 + 把 Disk image location 指到 `G:\Work\docker` |
| D4 | **T5** 的 P1-1 | 需要 Trae 三选一：他自己改 / 授权 CodeBuddy / 不做。Trae 自 09-17 起未回复 |
| D5 | **T8**：`dev-workbuddy` / `dev-codebuddy` 何时合回 `main` 并发布 | `main` 至今停在 `4cae06d`，落后两个分支 |
| D6 | **T3**：让 Redis 真正被用上 | 是否把 `RATE_LIMIT_BACKEND=redis` + `REDIS_URL` 写进 `config.env`，并加开机自启 |

---

## 11. 常用命令速查

```bash
# ---- 环境 ----
cd /d D:\Dev\recruitment-python
G:\Work\redis\start-redis.cmd                 # 启动 Redis（双击亦可）
G:\Work\redis\redis-cli.exe PING              # 应为 PONG

# ---- 测试 ----
.venv\Scripts\python.exe -m pytest -q --tb=line -p no:warnings

# ---- 起服务（源码模式）----
start.bat                                      # → http://localhost:8080
# 想启用 Redis 后端：
set RATE_LIMIT_BACKEND=redis
set REDIS_URL=redis://127.0.0.1:6379/0
start.bat

# ---- git ----
git log --oneline -8
git status --short                             # 注意有 33 个悬空条目，别 git add -A
git branch -v
git show dev-codebuddy:collab/MISSION-redis-docker.md    # 读对方分支的文件

# ---- 前端 ----
cd frontend && npm install --no-fund --no-audit && npm run build
```

---

## 12. 接手第一天建议做的 5 件事

1. **确认工作目录是 `D:\Dev\recruitment-python`**（判定特征见 §2），并确认分支是 `dev-codebuddy`。
2. **复跑全量测试**，确认基线到底是 736 还是别的数字：
   ```bash
   .venv\Scripts\python.exe -m pytest -q --tb=line -p no:warnings
   ```
   → 这是 A1，也是后续一切改动的前提。
3. **启动 Redis 并跑一次 A10 验收**（T4）——**这项不需要 Docker，可以立刻做**：
   本机起 2 个 uvicorn worker，用 `tests/test_circuit_breaker_redis.py` 里的对照实验对齐口径，
   验证「一个 worker 触发熔断后，另一个是否也进入熔断态」。
4. **通读 `collab/MISSION-redis-docker.md`**，特别是 §0（核心判断）、§3（硬性约束）、§4（A1-A10 判据）、§5（文件所有权）。
   然后读 `collab/inbox-codebuddy-reply.md`（CodeBuddy 的最新回信，含对我判断的纠正）。
5. **把 §10 的待决事项整理成一段话给用户** —— 尤其 D3（装 Docker）与 D6（让 Redis 真正用上），
   这两项直接决定「架构硬化」这个任务能不能收口。

---

## 13. 一句话总结

> 代码侧的 P0/P1 基本做完了（只剩 P0-2 与 P1-1），**卡在环境上**：
> **Docker 没装 → A7/A8 验不了；Redis 装了但没接进 `config.env`、也没开机自启 → 「真正用上」这一步没落地。**
> 另外 `app/config.py` 有 108 行历史悬空改动压着，会让跨分支 `checkout` 失败 —— 这件事需要用户拍板。
