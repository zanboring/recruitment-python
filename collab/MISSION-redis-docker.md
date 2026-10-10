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

---

## 8. 验收记录（WorkBuddy 填写）

> 格式：`## ACCEPT <日期 时间> <任务号> <结论>`，附判据与证据。

<!-- 待验收时追加 -->
