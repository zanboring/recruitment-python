# 任务书：Redis + Docker 架构硬化（面向「AI 全栈能力展示」）

> 发起：WorkBuddy（验收方）　｜　执行：CodeBuddy　｜　拍板：用户
> 创建：2026-10-10　｜　状态：**待 CodeBuddy 认领**

---

## 0. 目标与判断前提

**用户目标**：让项目体现**全面的 AI 全栈 + AI 应用开发能力**。
因此判据不是「够不够用」，而是「**架构是否完整、每个中间件是否真的承担职责**」。
Redis 与 Docker **必须有**，但不能是摆设。

### ⚠️ 核心判断（先读这一条，它决定整个任务的方向）

> **Redis 的价值只在多副本下成立。**
>
> 当前项目是单进程部署，所以下面这些实现**全都在进程内**，单副本时完全正确：
> 限流计数、爬虫日配额、AI 会话历史、熔断器状态、知识库缓存。
>
> 一旦水平扩展到 **2 个副本**，它们会同时失效：
> - 限流配额变成 **2 倍**（每个 worker 各算一份）→ 防爆破、防刷 LLM 全部形同虚设
> - 爬虫日配额变成 **2 倍** → 对招聘站点的合规承诺**不成立**
> - AI 会话历史**分片** → 同一个 session 在两台机器上对话会串台或丢失
> - 熔断器**各自为战** → 一台熔断、另一台继续打，故障放大
>
> **所以：Redis 化必须与多副本一起做。只加 Redis 不加副本 = 白加，且面试时会被问倒。**
> 反过来，这个改造本身就是最好的能力展示 —— 它证明你**知道单机实现的天花板在哪里**。

---

## 1. 现状核实（WorkBuddy 已实测，可直接引用）

| 编号 | 文件 | 现状实现 | 多副本下的后果 |
|---|---|---|---|
| R1 | `app/middleware/rate_limit.py:27-75` | `SlidingWindowLimiter` 用 `OrderedDict` + `threading.Lock`，纯进程内 | 配额 × N，防爆破失效 |
| R2 | `app/services/ai_service.py:12-89` | 会话历史进程内字典，`MAX_SESSIONS=1000`、`SESSION_TTL_SECONDS=3600` 手工淘汰 | 会话分片、串台 |
| R3 | `app/crawlers/throttle.py:85-191` | `DomainGate` 用 `asyncio.Lock`；`DailyQuota` 用 `defaultdict` 按北京日期计数 | **日配额 × N，合规承诺失效** |
| R4 | `app/utils/circuit_breaker.py:42-102` | `_state` / `_failure_count` / `_stats` 全在实例内，`threading.Lock` | 各自熔断，故障放大 |
| R5 | `app/cache.py` | **已支持 Redis（`redis.asyncio`），失败永久降级内存** —— 这条已经做对了 | ✅ 已具备，缺「证明跨进程生效」的演示 |

`app/config.py` 已有 `redis_url: str = ""`、`redis_cache_ttl: int = 600` 两个配置项，但本机未启用。

Docker 侧缺口：

| 编号 | 现状 | 问题 |
|---|---|---|
| D1 | **无 `.dockerignore`** | build context 含 `.venv`(138M)、`frontend/node_modules`(237M)、`.git`；且 `docker/frontend/Dockerfile:8` 的 `COPY frontend/ ./` 会把宿主的 `node_modules` 覆盖进 builder 阶段 |
| D2 | **零 healthcheck**，`depends_on` 裸用 | 只保证启动顺序、不保证就绪。nginx 先就绪时后端还没起来 → 前端 502 |
| D3 | 无日志轮转 | 长跑日志无限增长 |
| D4 | 单副本（无 `--workers`） | **Redis 的价值无法体现**（见第 0 节） |
| D5 | `docker/nginx.conf` 无 gzip / 静态缓存头 | 前端有 1MB 级 chunk，每次全量传输 |
| D6 | 无部署配置示例 | 部署方不知道要提供哪些变量（`JWT_SECRET` 的坑就源于此） |

**已修复项（勿重复）**：`frontend/dist` 产物已重建并端到端验证通过；全量测试 697 项全绿。

---

## 1.5 环境实况（2026-10-10 盘符变更后实测）

### 磁盘布局（已核实，与用户描述一致）

| 盘符 | 物理盘 | 型号 | 接口 | 容量 | 可用 | 卷标 |
|---|---|---|---|---|---|---|
| **C** | Disk 1 | WD_BLACK **SN850X** 2000GB | NVMe | 500 GB | 402.6 GB | Win11ProW X64 |
| **D** | Disk 1 | WD_BLACK **SN850X** 2000GB | NVMe | 1362.7 GB | 902.1 GB | Game |
| **E** | Disk 2 | WD_BLACK **SN7100** 1TB | NVMe | 931.5 GB | 617 GB | Game |
| **F** | Disk 3 | WDS100T1X0E-00AFY0（= WD_BLACK **SN850** 1TB） | NVMe | 501 GB | 337.3 GB | Game |
| **G** | Disk 3 | WDS100T1X0E-00AFY0（同一块盘） | NVMe | 430.5 GB | 430 GB | Work |
| **H** | Disk 0 | **ST2000DM001**-1ER164（希捷 2TB） | SATA | 1863 GB | 1680.9 GB | Repository |
| I | Disk 4 | Kingston DataTraveler 3.0 | USB | 57.8 GB | 49.9 GB | BACKUP（exFAT） |

- C 与 D 同属 **SN850X 2TB** 的两个分区 → 项目落在 D 盘，即**跑在最快的盘上**。
- ⚠️ **I 盘是插着的 USB 优盘**（卷标 `BACKUP`），用户未提及。注意别当固定盘依赖。
- 缓存位置都不在 C 盘：npm cache → `D:\Dev\caches\npm`；pip cache → `G:\Work\cache\pip`。

### 工具链实况

| 组件 | 状态 | 对验收的影响 |
|---|---|---|
| `.venv` | ✅ Python 3.14.7 | — |
| `frontend/dist` + `node_modules` | ✅ 已恢复 | — |
| `data/recsys.db` | ✅ 135 KB | — |
| **Ollama** | ✅ **已装且运行中**，`OLLAMA_MODELS=G:\Work\ollama\models` | 本地降级链可**真实演示**（见下） |
| **Docker** | ❌ **仍未安装**（无 Docker Desktop、无 docker CLI、无 `com.docker.service`） | ⛔ **A7 / A8 现在无法验收** |
| **Redis** | ✅ **已由 WorkBuddy 部署并运行中**（见下） | A3 / A5 / A6 **已可验收**；A4 待 P0-2 |

### 关键变化：Ollama 从「隐患」变成「资产」

盘符变更前 `OLLAMA_ENABLED=true` 是个坑（没装 Ollama → 请求先超时再降级）。
现在 Ollama 已装，且**模型与项目配置完全对齐**：

| 项目 `.env` 配置项 | 值 | Ollama 是否已拉取 |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3.5:4b` | ✅ 3.4 GB |
| `OLLAMA_CODE_MODEL` | `qwen3.5:4b` | ✅ |
| `OLLAMA_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | ✅ 639 MB |

环境变量另有：`OLLAMA_MAX_LOADED_MODELS=2`、`OLLAMA_NUM_PARALLEL=1`、
`OLLAMA_CONTEXT_LENGTH=8192`、`OLLAMA_FLASH_ATTENTION=1`。

→ **「云端 DeepSeek/智谱 → 本地 Ollama → 规则引擎」三级降级链现在可以离线、零成本完整演示。**
建议 CodeBuddy 在 P1 追加一项 **P1-6：提供「断网可用」演示路径说明**
（拔网线 → 自动走本地 Ollama → AI 功能仍出结果），这是 AI 应用能力里很有说服力的一环。

### ⚠️ 防误编辑：H 盘有 3 份旧副本，都不是活项目

| 路径 | 最后修改 | 特征 |
|---|---|---|
| `H:\Backup\Dev\recruitment-python` | 2026-09-28 | `main` 分支，**无** `collab/MISSION-redis-docker.md` |
| `H:\Backup\recruitment-python-workarea-20260928` | 2026-09-28 | 同上 |
| `H:\Backup\C_Users_20260924\Desktop\recruitment-python` | 2026-09-23 | 同上 |

**判定活项目**：`D:\Dev\recruitment-python` 有 `collab/MISSION-redis-docker.md`，
且分支为 `dev-workbuddy` / `dev-codebuddy`。
**动手前先确认路径是 `D:\Dev\recruitment-python`**，不要打开 H 盘备份改。

### 🟢 Redis 已部署（2026-10-10 13:41，WorkBuddy 完成）

| 项 | 值 |
|---|---|
| 版本 | **Redis 8.10.2**（免安装 Windows 版，`redis-windows/redis-windows` release 8.10.2 msys2） |
| 位置 | `G:\Work\redis`（不占 C 盘） |
| 启动 | 双击 `G:\Work\redis\start-redis.cmd`（或 `redis-server.exe recsys-redis.conf`） |
| 监听 | `127.0.0.1:6379`（仅回环） |
| 配置 | `G:\Work\redis\recsys-redis.conf` —— AOF `appendonly yes`（对齐 compose 的 `--appendonly yes`）、`maxmemory 512mb` + `allkeys-lru` |
| 数据 | `G:\Work\redis\data`；日志 `G:\Work\redis\logs\redis.log` |
| 能力自检 | ZSET ✅ / Lua EVAL ✅ / INCR+EXPIREAT ✅ / SET NX PX ✅ |

> ⚠️ `logfile` / `dir` 在 conf 里必须写**绝对路径**：Redis 会把相对的 `logfile` 解析到 `dir` 之下，
> 写 `./logs/redis.log` 会直接 `FATAL CONFIG FILE ERROR: Can't open the log file`。

**项目侧接入方式**（两个环境变量，二者需同时给）：

```bash
RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
```

不设这两项时项目走内存实现，行为与改造前**完全一致**（已验收 A2）。

### ⛔ 验收顺序因此需要调整（重要）

Docker 与 Redis 都还没装，所以**不能按 P0 顺序一次验收完**。建议改为：

| 阶段 | 做什么 | 依赖 | 可验收项 | 进度 |
|---|---|---|---|---|
| **第 1 阶段** | 装 Redis | 只需本机 | — | ✅ **已完成**（WorkBuddy，13:41） |
| **第 2 阶段** | CodeBuddy 做 P0-1 / P0-2 / P1-1 / P1-2 | Redis | **A3 / A4 / A5 / A6** —— 用**本机起 2 个 uvicorn worker** 验证跨进程语义，**不需要 Docker** | 🟡 P0-1 已交付并**验收通过**（A3/A5/A6 ✅）；P0-2 待做（A4 待补验） |
| **第 3 阶段** | 装 Docker Desktop（**Disk image location 指到 `G:\Work\docker`**，别吃 C 盘） | — | **A7 / A8** | ⬜ 待用户 |
| **第 4 阶段** | CodeBuddy 做 P0-3 / P0-4 / P0-5 / P1-3 / P1-4 | Docker | A7 / A8 | 🟡 代码已在进行中（`.dockerignore`、`.env.docker.example`、compose 改动已在工作树），等 Docker 装好才能验 |

> 用户已在 G 盘预留 `G:\Work\docker` 与 `G:\Work\wsl` 目录，装 Docker Desktop 时直接指过去即可。

---

## 2. 任务分解

### P0 —— 必做（缺一项则能力展示不成立）

| # | 任务 | 落点文件 | 要点 |
|---|---|---|---|
| P0-1 | **Redis 滑动窗口限流** | `app/middleware/rate_limit.py` | 用 Lua 脚本实现原子滑动窗口（`INCR`/`EXPIRE` 的固定窗口有边界双倍突发问题，原代码注释已点明要用 Lua）。**保留内存实现作为降级路径** |
| P0-2 | **爬虫全局日配额 + 域名节流走 Redis** | `app/crawlers/throttle.py` | `DailyQuota` 改 Redis 计数器（按北京日期做 key 并设过期）；`DomainGate` 的跨进程串行需要用 Redis 锁或 `SET NX PX` |
| P0-3 | **多副本部署** | `docker-compose.yml` + backend 启动命令 | `uvicorn --workers N` 或 compose 多实例；**这是 Redis 生效的前提，必须一起做** |
| P0-4 | **`.dockerignore`** | 新增 | 至少排除 `.venv/`、`frontend/node_modules/`、`frontend/dist/`、`.git/`、`__pycache__/`、`*.pyc`、`reports/`、`.workbuddy/`、`.codebuddy/` |
| P0-5 | **healthcheck + 就绪依赖** | `docker-compose.yml` | redis / backend 各加 `healthcheck`；`depends_on` 改 `condition: service_healthy` |
| P0-6 | **部署配置示例** | 新增 `.env.docker.example` | 列出必填项（`JWT_SECRET` 等）与生成命令；顺带修掉「compose 只读 `.env` 不读 `config.env`」导致 `JWT_SECRET` 必填报错却指向不明的坑 |

### P1 —— 加分（体现深度，时间允许就做）

| # | 任务 | 落点文件 | 要点 |
|---|---|---|---|
| P1-1 | **AI 会话外置 Redis** | `app/services/ai_service.py` | 会话历史 + TTL 交给 Redis（`EXPIRE` 天然解决淘汰），保留进程内为降级 |
| P1-2 | **熔断器状态共享** | `app/utils/circuit_breaker.py` | 全局熔断，避免「一台已熔断、另一台继续打」 |
| P1-3 | **nginx gzip + 静态缓存头** | `docker/nginx.conf` | `gzip on`（含 `text/javascript`/`application/javascript`）；`/assets/` 加长缓存（Vite 产物文件名带 hash） |
| P1-4 | **日志轮转** | `docker-compose.yml` | `logging.options.max-size` / `max-file` |
| P1-5 | **一键验收脚本** | 新增脚本 | `docker compose up` 后自动跑冒烟（见第 4 节判据） |

### 明确不做（避免过度设计）

- ❌ 不引入 Celery / Kafka / RabbitMQ 等额外中间件 —— 目标是展示，不是造分布式系统
- ❌ 不改业务语义、不改接口契约、不改前端业务代码
- ❌ 不为「演示」硬编码任何假数据或假指标

---

## 3. 硬性工程约束（验收会逐条检查）

1. **降级不可丢**：所有 Redis 化改造必须有开关（命名建议 `RATE_LIMIT_BACKEND=memory|redis`、`QUOTA_BACKEND=memory|redis`、`SESSION_BACKEND=memory|redis`），且**新配置项的默认值必须保持现有单机行为不变** —— `redis_url` 为空时，项目必须能和现在一样完整跑起来。
   > 理由：`app/cache.py:1-14` 已经把「缓存是加速手段、不是正确性依赖」定为设计约束，新代码必须延续这条原则。
2. **测试不许退**：现有 697 项测试必须保持全绿。新增能力要有自己的用例（建议 `tests/test_rate_limit_redis.py`、`tests/test_throttle_redis.py`），且用例**不得依赖真实 Redis 进程**（用 mock 或 `fakeredis`），否则 CI 跑不了。
3. **配置项加在 `app/config.py` 的 `Settings` 类末尾自己区块**（见 `collab/README.md` §5 共用文件规则），不要插进别人区块中间。
4. **`app/main.py` 的导入行与 `include_router` 追加到末尾**，冲突时通常「两边都保留」。
5. **提交前自检**：`.venv/Scripts/python.exe -m pytest -q --tb=line -p no:warnings` 必须全绿。

---

## 4. 验收标准（WorkBuddy 执行，逐条给证据）

> 判据原则：**每个 Redis 化改造都必须给出「多副本下才成立」的证据**，不接受「代码改成 Redis 了」这种自证。

| # | 验收项 | 判据（可执行） |
|---|---|---|
| A1 | 全量测试 | `pytest -q --tb=line -p no:warnings` → **0 failed**，总数 ≥ 697 |
| A2 | 单机不回归 | 不设 `REDIS_URL` 启动 → 根路径 200 且含 `<div id="app">`；`/api/system/version` 返回 401（需鉴权）；行为与改造前一致 |
| A3 | 限流跨副本生效 | 起 **2 个 worker**，用同一身份并发打 `POST /api/auth/login`，**总放行数 ≤ 配置的 `rate_limit_login_per_minute`**（内存版会是 2 倍 → 必须看到只有 1 倍） |
| A4 | 日配额跨副本生效 | 2 worker 下触发采集，平台累计访问数 **≤ `CRAWL_DAILY_QUOTA_PER_PLATFORM`** |
| A5 | Redis 掉线可降级 | 关掉 Redis 后重启 → 服务仍能启动、根路径仍 200、业务接口无 5xx（只记 warning） |
| A6 | 缓存跨进程一致 | 进程 A 写缓存 → 进程 B 读得到；知识库变更后 A 清缓存 → B 也读不到旧值 |
| A7 | compose 与镜像 | `docker compose config` 校验通过；`.dockerignore` 生效后 build context 明显下降（给出前后对比数字） |
| A8 | 就绪依赖 | `docker compose up` 后前端首屏**不出现 502**；`docker compose ps` 显示 healthcheck 为 healthy |
| A9 | （P1-1 若做）会话跨副本 | 2 worker 下同一 `session_id` 连续多轮对话，历史不丢不串 |
| A10 | （P1-1 若做）熔断跨副本 | 一个 worker 触发熔断 → 另一个 worker 也进入熔断态 |

**A3 / A4 / A6 是本任务的核心证据** —— 这三条拿不出，整个改造就还是「装了但没用」。

---

## 5. 文件所有权与边界声明（**动手前必读**）

按 `collab/README.md` §5 现行分工表，本任务会跨越既有边界，**需要先声明再动**：

| 文件 | 现行归属 | 本任务是否触碰 | 处理 |
|---|---|---|---|
| `app/crawlers/**`、`app/services/crawler_service.py` | **WorkBuddy 独占** | ✅ 触碰（P0-2 `throttle.py`） | **WorkBuddy 自己改**，CodeBuddy 不要动 |
| `app/services/ai_service.py` | **Trae**（稳定性链路） | ✅ 触碰（P1-1） | 动手前必须发消息给 Trae 征得同意 |
| `app/services/llm_client.py`、`job_checker.py` | **Trae** | ❌ 不碰 | — |
| `recsys.spec`、`build-exe.bat`、`run_entry.py`、`start*.bat`、`git-sync.cmd` | **Trae**（打包发布） | ⚠️ 可能需要（多副本启动命令） | 优先改 `docker-compose.yml` 的 command，**不改 `start*.bat`**；确需改动先发消息 |
| `docker-compose.yml`、`docker/**` | 未明确（部署域） | ✅ 触碰（P0-3/4/5） | **本任务书即视为边界声明的依据**；同时给 Trae 发一条知会消息 |
| `app/middleware/rate_limit.py` | 未明确 | ✅ 触碰（P0-1） | 同上 |
| `app/utils/circuit_breaker.py` | 未明确（稳定性相关） | ✅ 触碰（P1-2） | 先发消息给 Trae |
| `app/config.py` | 共用 | ✅ 触碰 | 新增项**追加到 `Settings` 类末尾自己区块** |
| `app/main.py` | 共用 | ⚠️ 可能 | 导入行与 `include_router` 追加末尾 |
| `tests/**` | 各自新增 | ✅ | 只新增自己的测试文件，**不改对方的测试文件** |
| `collab/**` | WorkBuddy（`inbox-workbuddy.md` 除外） | ✅ | 由 WorkBuddy 维护 |

**必须发出的边界知会**：向 `collab/inbox-trae.md`（WorkBuddy 写信道）追加一条，说明本任务将触碰
`docker-compose.yml` / `docker/**` / `app/middleware/rate_limit.py` / `app/utils/circuit_breaker.py`，
并询问 `app/services/ai_service.py` 是否可改。

---

## 5.5 分支约定（2026-10-10 起生效 · 用户要求）

> **用户明确要求：WorkBuddy 与 CodeBuddy 都写自己的分支，不再向 `main` 直接提交。**

| 分支 | 谁写 | 基点 | 现状 |
|---|---|---|---|
| `dev-workbuddy` | **WorkBuddy** | `main` @ `4cae06d` | 已建，含任务书与协作信道提交 `4bdfa97` |
| `dev-codebuddy` | **CodeBuddy** | `dev-workbuddy` @ `4bdfa97` | 已建（**含任务书，开箱即可读**） |
| `main` | 用户拍板后合并 | — | 停在 `4cae06d`，**未被污染** |

### CodeBuddy 开工三步

```bash
# 1) 切到自己的分支
git -C D:\Dev\recruitment-python checkout dev-codebuddy

# 2) 确认（应输出 dev-codebuddy）
git -C D:\Dev\recruitment-python rev-parse --abbrev-ref HEAD

# 3) 提交时逐个列出自己的文件（见下方警告，禁止 git add -A）
git add app/middleware/rate_limit.py tests/test_rate_limit_redis.py
git commit -m "feat(ratelimit): Redis Lua 滑动窗口限流 + 内存降级"
```

### ⚠️ 工作树里有 23 个「非本任务」的未提交改动 —— 不要碰

**已修改（15 个）**：`app/config.py`、`app/crawlers/base.py`、`app/crawlers/boss.py`、
`app/dependencies.py`、`app/evaluation/runner.py`、`app/init_data.py`、`app/routers/compat.py`、
`app/services/crawler_service.py`、`app/services/knowledge_service.py`、`app/services/llm_client.py`、
`app/utils/circuit_breaker.py`、`docker-compose.yml`、`reports/rag_evaluation.md`、
`userscript/recsys-collector.user.js`、`frontend/package-lock.json`

**未跟踪（8 个）**：`.codebuddy/`、`ENV_MIGRATION_CHECKLIST.md`、`LEARNING_PATH.md`、
`PROJECT_SHOWCASE.md`、`reports/Windows.old*.html`（2 个）、`reports/audit_2026-10-0*.html`（2 个）

**处理原则**：**不要提交、不要还原、不要 stash。** 这些是别人（或历史会话）在飞的活。
提交时**逐个列出自己的文件路径**，**严禁 `git add -A` / `git add .`**，
否则会把不相干的半成品一起卷进提交，之后无法分离。

> 注：`frontend/package-lock.json` 的改动是 **WorkBuddy 本轮 `npm install` 的副产物**
> （为恢复 `frontend/dist` 而安装依赖）。已单独披露，未提交，由用户决定去留。

### 分支同步与一个历史遗留问题

`git push` 在当前沙箱被网络拦（见 `COLLAB.md` §5），两个新分支**目前只在本地**。
需要推远端时由用户执行：

```bash
git push origin dev-workbuddy dev-codebuddy
```

> ⚠️ **远端已存在一个旧的 `origin/dev-workbuddy`，与本地新分支已分叉**
> （`main` 领先它 7 个提交，它领先 `main` 3 个）。首次推送本地 `dev-workbuddy` **会被拒绝**。
> 这是历史遗留问题，**请单独决策，不要在本任务里顺手处理**（避免把两件事混在一起）。

---

## 6. 协作流程（三方）

```
① CodeBuddy 认领  →  按 P0 → P1 顺序实现，每个 P0 完成即自测
② CodeBuddy 交付  →  更新本文件「交付记录」区，写明：改了什么、怎么验证的、证据在哪
③ WorkBuddy 验收  →  按第 4 节 A1-A10 逐条跑，输出「通过/不通过 + 证据」
④ 用户拍板        →  决定是否合入 main / 是否发布
```

**规则**：
- CodeBuddy 每完成一个 P0 就写一条记录（不要攒到最后一次性交付，否则问题会堆积）
- 验收不通过 → WorkBuddy 给出**可复现步骤**，不做「我觉得不行」这种无效反馈
- 双方都**不要直接改 `main`**；建议在 `dev-workbuddy` / 独立 worktree 上作业
- ⚠️ 当前工作区在 `main` 上且有 14 个未提交文件 —— **动手前先归位**

---

## 7. 交付记录（CodeBuddy 填写）

> 格式：`## DELIVER <日期 时间> <任务号> <标题>`，只追加不修改历史行。

<!-- 待 CodeBuddy 追加 -->

## DELIVER 2026-10-10 13:37 P0-1 Redis 滑动窗口限流（Lua + ZSET，内存降级）

**改了什么**

| 文件 | 改动 | 与在飞改动是否重叠 |
|---|---|---|
| `app/middleware/rate_limit.py` | 新增 `RedisSlidingWindowLimiter` + `_SLIDING_WINDOW_LUA`；`RateLimitMiddleware` 新增 `_check()` 统一出口（Redis 优先、不可用降级内存）。**内存版 `SlidingWindowLimiter` 与 `default_limiter` 一行未改** | 无（该文件此前干净） |
| `app/config.py` | `Settings` 类末尾新增「Redis + Docker 架构硬化（CodeBuddy 区块）」，加 `rate_limit_backend: str = "memory"` | **有** —— 已用 patch 方式只暂存本任务 12 行，未卷入他人改动 |
| `tests/test_rate_limit_redis.py` | 新增 20 项用例（新建文件，不依赖真实 Redis） | 无 |

**四条关键设计（面试可讲）**

1. **为什么必须是 Lua 而非 INCR + EXPIRE**：INCR 是固定窗口，窗口边界会双倍突发（前后两窗口各放行 limit 个）。ZSET + 时间戳天然滑动，但「清理过期 → 计数 → 写入」三步必须原子，否则并发下两个请求会同时读到 `count = limit-1` 而都放行；Lua 在 Redis 单线程内执行，天然原子。
2. **ZSET 的 member 必须唯一**：member 相同是覆盖而非累加。若拿毫秒时间戳当 member，同毫秒并发请求会互相覆盖 → 计数偏小 → 限流失效。实现用 `f"{now_ms}-{uuid4().hex[:12]}"`。
3. **降级契约**：`RedisSlidingWindowLimiter.check()` 在 Redis 不可用时返回 `None`（**不抛异常**），中间件回退内存 —— 限流是保护手段，不能因 Redis 故障变成 5xx 来源。连接/脚本失败进 30s 冷却，避免每请求都白等一次连接超时。
4. **默认值保持单机行为**：`rate_limit_backend` 默认 `memory`；`REDIS_URL` 为空时连惰性建连都不做 ——「不配 Redis 时项目与改造前完全一致」。

**怎么验证 / 证据在哪**

| # | 验证项 | 入口 / 命令 | 实测结果 |
|---|---|---|---|
| 1 | 全量测试 | `.venv/Scripts/python.exe -m pytest -q --tb=line -p no:warnings` | **717 passed / 0 failed**（697 → 717，+20） |
| 2 | **A2 单机不回归** | TestClient 走完整 lifespan（不设 `REDIS_URL`） | `ROOT_STATUS=200`、`HAS_APP_DIV=True`、`/api/system/version=401`（预期） |
| 3 | 默认后端限流仍生效 | 连打 12 次 `POST /api/auth/login`（limit=10） | `[401×10, 429, 429]` |
| 4 | **跨副本共享计数** | `test_redis后端跨副本共享计数_总放行等于上限` | 2 实例共享同一 Redis → 总放行 **= limit** |
| 5 | 对照组（证明 #4 不恒真） | `test_对照_内存后端双副本放行翻倍` | 内存后端 2 实例 → 总放行 **= 2 × limit** |

**⚠️ 诚实局限（A3 需在有真实 Redis 的环境验收）**

`tests/test_rate_limit_redis.py` 用自写替身 `FakeRedis`（符合约束「新用例不得依赖真实 Redis 进程」），
代价是：**Lua 脚本本身的正确性无法在单测里被证明**（替身是 Python 版同一算法）。
真实 Redis 下的 A3（起 2 worker 并发打登录接口、总放行 ≤ 配置上限）**本机无法实测** ——
环境未安装 Docker / Redis（已确认 `docker` 命令不存在）。
请验收方在有 Redis 的环境执行；如需 `fakeredis` 版本（需新增依赖），我未擅自引入，可另行授权。

## DELIVER 2026-10-10 13:42 P0-4 .dockerignore（+ 修 Dockerfile 一处必然失败的语法）

**改了什么**

| 文件 | 改动 |
|---|---|
| `.dockerignore` | 新增。排除依赖目录 / 构建产物 / 字节码缓存 / 密钥文件 / 本地数据 / 开发过程产物 / AI 工具元数据 |
| `docker/backend/Dockerfile` | 删除 `COPY reports ./reports 2>/dev/null || true` |

**为什么删那行**：`2>/dev/null || true` 是 shell 语法，**Dockerfile 的 COPY 不支持**——它会被解析成多个源路径（`reports`、`./reports`、`2>/dev/null`、`||`）+ 目标 `true`，源不存在即构建失败。而 `reports/` 本就是**运行时产物目录**（`report_service._write_excel_file` 会 `mkdir(parents=True)`），且已列入 `.dockerignore`，正确做法是**不 COPY**。

**为什么不能写 `*.html`**：`app/static/index.html` 是前端产物缺失时的兜底页（`main.py::_resolve_static_index`）。已实测 `app/` 下唯一的 html 就是它 —— 宽规则会把它一起排除，镜像就丢了 fallback。

**怎么验证 / 证据在哪**

| # | 验证项 | 结果 |
|---|---|---|
| 1 | **A7 体积对比** | 不排除 **≈ 429.4 MB** → 排除后 **≈ 2.6 MB**。分项实测：`.venv` 215 MB、`frontend/node_modules` 201.9 MB、`reports` 4.1 MB、`tests` 1.8 MB、`.git` 1.4 MB |
| 2 | 覆盖任务书要求的排除项 | `.venv/`、`frontend/node_modules/`、`frontend/dist/`、`.git/`、`__pycache__/`、`*.pyc`、`reports/`、`.workbuddy/`、`.codebuddy/` 全部覆盖 |
| 3 | 兜底页不被误伤 | `app/static/index.html` 存在且不被任何规则匹配（刻意不用 `*.html` / `app/**` 宽规则） |
| 4 | 密钥不进 context | `.env` / `config.env` / `config.template.env` 已显式排除 |

**⚠️ 证据局限**：本机**未安装 Docker**（`docker --version` 不存在），`docker compose config` 与真实 `docker build` 无法执行；体积对比以「文件系统体积求和」等价替代，未经 Docker daemon 实测。请在具备 Docker 的环境复核 A7。

## DELIVER 2026-10-10 13:45 P0-3 多副本 + P0-5 healthcheck/就绪依赖 + P0-6 部署模板（含 P1-4 日志轮转）

**改了什么**

| 文件 | 改动 |
|---|---|
| `docker-compose.yml` | ① backend 增加 `command`：`uvicorn --workers ${BACKEND_WORKERS:-2}`，`init_db` 在 fork 前只执行一次 ② redis / backend / frontend 各加 `healthcheck` ③ 两处 `depends_on` 改为 `condition: service_healthy` ④ 新增 `RATE_LIMIT_BACKEND=${RATE_LIMIT_BACKEND:-redis}` ⑤ 三服务加 `logging` 轮转（P1-4）⑥ 顶部提示指向 `.env.docker.example` |
| `.env.docker.example` | 新增。列出必填项与生成命令；**明确写出「Compose 插值只读 `.env`、不读 `config.env`」**（P0-6 要修的坑） |

**关键设计**

1. **为什么选 `uvicorn --workers` 而不是 compose 多实例**：A3 判据即「起 2 个 worker」，同容器多进程直接满足，且不必引入 nginx upstream 额外一层。`init_db` 放 `&&` 左侧、在 fork 之前只执行一次。
2. **healthcheck 为什么用 `/openapi.json`**：`/api/system/version` 需鉴权（401），不适合探活；`/openapi.json` 无需鉴权且能证明 FastAPI 已就绪。镜像内无 `curl`，用基础镜像自带的 python。
3. **就绪依赖直接服务 A8**：`frontend` 等 `backend: service_healthy` 才启动 nginx —— 否则 nginx 先起时代理到尚未监听的后端，前端首屏 502。

**⚠️ 必须记录的副作用：多副本下进程内调度器会重复触发**

`--workers 2` 后，`app/main.py` lifespan 里的 APScheduler（定时爬取 / 日报）与 `job_checker`
会在**每个 worker 内各自启动** → 定时任务重复执行 N 次，对招聘站点压力翻倍。
这是多副本的真实代价（进程内调度器无法感知兄弟进程），已写进 compose 注释与 `.env.docker.example`，
并给出两条出路：a) 生产关掉进程内调度、改用外部 cron 调 API；b) 后续用 Redis 锁选主。
**本任务范围内不修**（属独立课题），但不掩盖。

**怎么验证 / 证据在哪**

| # | 验证项 | 结果 |
|---|---|---|
| 1 | compose 语法（A7 前半） | `yaml.safe_load` 解析通过；三服务结构、两处 `condition: service_healthy`、三处 healthcheck、三处 logging 全部断言为真 |
| 2 | 多副本命令正确性 | `--workers ${BACKEND_WORKERS:-2}` 存在；`init_db.py` 在 fork 之前 ✅ |
| 3 | 单机不回归（A1/A2） | 全量 **717 项通过**；TestClient 走完整 lifespan：根路径 200 且含 `<div id="app">`、`/api/system/version` 401（预期） |
| 4 | 部署模板可用性 | `.env.docker.example` 含必填项、生成命令、「只读 .env」说明与定时任务副作用说明；不被 `.gitignore` 的精确 `.env` 规则匹配（可入库），且在 `.dockerignore` 中被排除（不入镜像） |

**⚠️ 证据局限**：本机未安装 Docker —— A7 后半（`docker compose config` 通过）、
A8（首屏不出现 502、`docker compose ps` 显示 healthy）、A3（2 worker 真实并发）**均需在有 Docker 的环境验收**。
我用 YAML 解析替代了 compose 的语法校验，但它**不覆盖 compose 自身的变量插值校验**（如 `${VAR:?}` 的行为）。

---

## 8. 验收记录（WorkBuddy 填写）

> 格式：`## ACCEPT <日期 时间> <任务号> <结论>`，附判据与证据。

## ACCEPT 2026-10-10 13:52 P0-1 ✅ 通过（Redis Lua 滑动窗口限流）

**验收方式**：WorkBuddy 独立执行，全部在**真实 Redis 8.10.2**（验收方本机新部署）+ **真实多进程**上复现。
**不采信自证** —— CodeBuddy 用自写 `FakeRedis` 替身，Lua 脚本本身的正确性无法在单测里被证明，
这一层正由本次验收补上。

### 逐条判定

| 项 | 判据 | 实测结果 | 结论 |
|---|---|---|---|
| A1 全量测试 | `pytest -q --tb=line -p no:warnings` | **717 passed / 0 failed**（697 → 717，+20） | ✅ |
| A2 单机不回归 | 不设 `REDIS_URL` + 默认 `memory` 后端 | 限流行为与改造前一致；全量测试（默认 memory）全绿 | ✅ |
| **A3 限流跨副本** | 2 进程交替并发打 `POST /api/auth/login`，总放行 ≤ limit | **redis 后端：10**（= limit）；**memory 对照组：20**（= 2×limit） | ✅ |
| A5 Redis 掉线可降级 | `REDIS_URL` 指向死端口 `6399` | 启动成功；14 次请求 → `[401×10, 429×4]`，**5xx = 0** | ✅ |
| A6 缓存跨进程 | 进程A 写 → 进程B 读 | 配 Redis：键 `a6probe` 真实存在于 Redis，进程B 读到 `shared-value`；不配 Redis 对照组：读到 `None` | ✅ |
| A4 爬虫日配额 | 2 进程累计访问 ≤ 配额 | **未验收** —— P0-2 尚未实现（`app/crawlers/throttle.py` 未改动） | ⏸️ 待 P0-2 |

### 核心证据：A3 的对照组不可省

| 后端 | 进程数 | 总放行 | 期望 | 说明 |
|---|---|---|---|---|
| `RATE_LIMIT_BACKEND=redis` | 2 | **10** | == limit | 全局配额一致 |
| `RATE_LIMIT_BACKEND=memory` | 2 | **20** | == 2 × limit | **配额翻倍，防爆破形同虚设** |

对照组证明该测试**不恒真**。内存后端在 2 副本下配额确实翻倍 ——
这正是「**只加 Redis 不加副本等于白加**」的实证，也是本任务存在的理由。

### 复现命令（验收方实际执行）

```bash
# 起 Redis（首次）
G:\Work\redis\start-redis.cmd

# A3：两个进程交替打登录接口，统计 401（放行）与 429（拦截）
#     分别在 RATE_LIMIT_BACKEND=redis / memory 下各跑一轮，对比总放行数
```

### 代码审查结论（附带，非验收项）

审 `9b411d1`，逻辑正确，四点站得住：

1. **必须用 Lua 而非 `INCR`+`EXPIRE`**：`INCR` 是固定窗口，窗口边界会双倍突发。
   ZSET + 时间戳天然滑动，但「清理过期 → 计数 → 写入」三步必须原子，
   否则并发下两个请求会同时读到 `count = limit-1` 而都放行；Lua 在 Redis 单线程内执行，天然原子。
2. **ZSET member 必须唯一**：member 相同是覆盖不是累加。用毫秒时间戳当 member，
   同毫秒并发会互相覆盖 → 计数偏小 → 限流失效。实现用 `f"{now_ms}-{uuid4().hex[:12]}"` ✔
3. **降级契约**：`check()` 不可用时返回 `None` **不抛异常**，中间件回退同步内存实现（该实现一行未改）；
   30s 连接冷却避免故障期每请求都白等一次超时。
4. **`_redis_enabled()` 双条件**：需显式 `RATE_LIMIT_BACKEND=redis` **且** 有 `REDIS_URL`；
   `redis_url` 为空时连惰性建连都省掉 → 这是 A2「单机不回归」的结构性保证。

### 两点观察（不阻塞，供后续决策）

1. **进程退出时的 asyncio 噪音**：一次性脚本里 `asyncio.run(cache.set(...))` 结束后会打印
   `RuntimeError: Event loop is closed`（`cache` 未显式关闭 Redis 连接）。
   uvicorn 长驻进程下不出现，**不影响功能**；若要洁癖，可在 `lifespan` 收尾 `await cache.close()`。
2. **两条独立 Redis 连接**：`app/cache.py` 的连接（`decode_responses=True`、无冷却）
   与限流器的连接（带 30s 冷却）是分开的。功能无碍，但「健康探测」口径不一致，
   P1 阶段可考虑收敛为一个连接工厂。

### 结论

**P0-1 验收通过。** 请 CodeBuddy 继续 P0-2（爬虫全局配额），完成后 A4 由本验收方补验。
