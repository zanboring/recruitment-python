# 已知边界与验证状态

> 这份文件区分「实现到什么程度」和「验证到什么程度」。每条都能指到代码位置、原始命令或审计报告；没有证据的不写。
> 数据口径来自 2026-11-11 只读快照 `recsys.db`（`mode=ro`）；代码对照只读副本 HEAD `880e2d7`。
> 本文件只记录现状，不含待办计划。
> 相关运行手册：[`backfill-playbook.md`](backfill-playbook.md)（回填脚本准入条件、参数、红线与 resume 边界）。

## 一、一览表

| 能力 | 实现 | 验证 | 一句话边界 |
|---|---|---|---|
| 数据库 | 部分 | 仅代码核对 + SQLite 实测 | ORM 与连接串构造支持 SQLite/MySQL/PostgreSQL；实测只跑过 SQLite，MySQL 未验证，PostgreSQL 有一处 `func.date` 不兼容 |
| Redis 跨副本共享（限流 / 配额 / 熔断 / 会话） | 完整（后端切换 + 内存降级接线） | 单测覆盖 Python 替身；真实 Redis 未验证 | 双实例 + 替身对照过机制；真实 Redis 上 Lua 原子性无执行记录 |
| Docker Compose 启动 | 完整编排 | 代码静态核对，未在本工位实跑 | 有前置条件：先配 `.env`、首次构建联网；`/api/` 入口请求体上限 12m，应用导入上限 10MB |
| 多 worker × SQLite 写 | compose 默认 2 worker | 未验证 | 跨进程写竞争零用例；现有并发实验均为单进程 asyncio |
| RAG 检索效果 | 完整（关键词 / 语义 / 混合三策略，黄金集 20 知识 / 28 查询） | **2026-11-16 已复现**（`qwen3-embedding:0.6b`） | 93.8%（语义型子集）/ 96.4%（整体）已复现并与 2026-10-10 补记一致；**换模型前**的 43.8% / 67.9% 仍只有人背书（旧模型本机未安装） |
| 详情回填 | 完整 | **实测跑过 332 条**（2026-11-16 夜）：完整正文 16/555 → 270/555；`detail_html` 仍 0/555 | 唯一取数路径是搜索接口 JSON，拿不到整页 HTML；详情页未登录态打不开；正文带来的技能增量未回写 `skills` 列 |
| 回填止损（连续被拦停批） | 完整 | 离线 stub + 单测 + 真站 332 条实测 | 搜索兜底路径已补风控判据；**阈值 5 在真实流量下未触发**（最长连续 3 条），只验证了计数增长与延迟拉大 |
| 技能抽取 | 完整（封闭词表字面命中） | 16 条小样本定性对照；无标注 ground truth | 不做语义理解；词表外技能抽不到；样本收益未回写库内 `skills` 列 |
| 推荐 / 技能画像 / 技能分布 | 完整 | 只读 SQL + 代码阅读，未起服务端到端验证 | 推荐候选池与技能画像口径为 NEW + ACTIVE（555 条）；本地模型页的技能 / 城市 / 学历分析仍只数 ACTIVE（42 条）；推荐链路不读岗位正文 |
| 薪资预测 | 完整 | 代码静态核对 | 规则系数纯函数，系数硬编码；不是从历史数据训练的模型 |
| 分布统计（城市 / 经验 / 学历 / 薪资） | 完整 | 只读 SQL 实测 | 不依赖岗位正文；可见口径分母 555 条；经验在统计层归并成 6 个桶（含「其他」，`job_service.py:41-55`） |
| 看板 / 图表展示 | 完整（前端调用链已逐行核对） | 代码静态核对 + 只读 SQL；未起服务 | 平均薪资、企业数量、分析摘要、DataCompare 四处展示缺陷已修；残留口径不一致见 §11 |
| 声明式规格引擎 | 部分（HTML / CSS / JSON 路径） | 抽取层在公开示例上验证过；真实招聘站未打通 | `type: api` 禁止 `detail.*`；已登记 boss / 51job / quotes_demo，51job 搜索接口被 WAF 拦在 HTML |
| RAG 问答 | 完整 | 代码静态核对 | 知识库 21 条，与岗位库独立；不能回答「这条岗位具体要求什么」 |
| 测试基线 | 完整 | 文档声明 1187 项 / 75 个测试文件；本工位未复跑 collect-only | 75 个测试文件为静态实测；1187 项含 parametrize 展开，读源码数不出，数字以 `docs/showcase.md` 声明为准 |

## 二、逐条说明

### 1. 数据库：支持三库，验证过一种

- **实际到哪一步**：ORM 层是 SQLAlchemy 2.0 异步方言写法，三种连接串构造都在（`app/database.py:55` PostgreSQL、`:58-59` SQLite、`:69` MySQL）。实测跑过的只有 SQLite：CI 无数据库服务、测试库强制内存 SQLite；Docker 栈把 `DB_TYPE` 钉在 sqlite。MySQL 路径未验证；PostgreSQL 在 AI 用量日报处有一处已知方言不兼容。
- **证据**：
  - `docker-compose.yml:30` `DB_TYPE: sqlite`；services 仅 backend / frontend / redis（`docker-compose.yml:13`），无 MySQL/PG 服务。
  - `.github/workflows/ci.yml` 无 `services:` 块；`:27-30` 四项后端显式 `memory`；`tests/conftest.py:27` 兜底 `DB_URL`、`:39` `TEST_DB_URL` 均为 `sqlite+aiosqlite:///:memory:`。
  - `app/services/usage_service.py:219-220` `func.date(AiUsage.created_at)`；`:219` 注释写明兼容目标是 SQLite 与 MySQL 两者。
  - `requirements.txt:18` `aiomysql==0.2.0` 锁版本；`:19` `asyncpg` 未锁版本。
  - 唯一碰到 mysql/postgres 字样的用例 `tests/test_db_engine_dialect.py:32-41` 只断言 URL→连接池 kwargs 映射，不连库。
- **现在怎么讲**：ORM 层支持三库，验证过 SQLite 一种；不要讲「三库可切」，也不要讲「不支持 MySQL/PG」。

### 2. Redis 跨副本共享：能力已实现，真实 Redis 未验证

- **实际到哪一步**：限流 / 日配额 / 熔断 / AI 会话四类后端都可切 Redis，故障自动降级内存；接线与降级契约用同一进程内两个 app 实例 + Python 语义替身对照过。生产侧的 Lua 脚本没有任何用例在真实 Redis 上执行过。
- **证据**：
  - 对照用例 `tests/test_rate_limit_redis.py:231`（Redis 版双实例只放行一份）、`:253`（内存版双实例翻倍）；替身 `:29 class FakeRedis`；`:7-11` docstring 自陈「Lua 脚本本身的正确性无法在这里被证明」。
  - 生产 Lua：`app/middleware/rate_limit.py:108,:210`；`app/crawlers/throttle.py:63,:368`；`app/utils/circuit_breaker.py:159,:286,:301,:318`。
  - `scripts/verify_redis_docker.py:20-24` 明写 A3/A4（真实 Redis + 2 worker 并发压测）等判据刻意不自动跑，留人工清单。
- **现在怎么讲**：跨副本共享的接线与降级契约已实现并经双实例对照；真实 Redis 上的 Lua 原子性、真双进程并发组合未验证。

### 3. Docker Compose：有前置条件，不是开箱即起

- **实际到哪一步**：compose 编排了后端 / 前端 / Redis 三个服务；启动有前置条件；请求体入口上限已在 nginx 层放开。
- **证据**：
  - 先准备根目录 `.env`（模板 `.env.docker.example`）；`JWT_SECRET` 不配会故意起不来（fail-fast），长度不足 32 字符生产环境拒绝启动：`docker-compose.yml:32-36`、`app/config.py:576,:595-596`。
  - 首次构建联网下载 npm / pip / Playwright chromium；镜像源参数见 `docker-compose.yml:18-22` 注释。
  - `/api/` 反代位置设 `client_max_body_size 12m`（`docker/nginx.conf:74`）；应用侧导入上限 `app/services/export_service.py:31` `IMPORT_MAX_BYTES = 10MB`；对外唯一端口 `8080:80`（`docker-compose.yml:96`）。
- **现在怎么讲**：能起，但要同时说明 `.env` 前置、首次联网；不要讲「一键启动」。

### 4. 多 worker × SQLite：跨进程写竞争未验证

- **实际到哪一步**：compose 默认 2 worker 共享一个 SQLite 文件（`docker-compose.yml:27` `--workers ${BACKEND_WORKERS:-2}`、`:30` sqlite）。仓库内并发写用例全部是单进程 asyncio；跨进程写锁路径零用例。
- **证据**：
  - `tests/` 目录 grep `workers|multiprocessing|Process(|Popen|fork` 0 命中；唯一并发写用例 `tests/test_crawler_validation.py:1030` 8 个协程共用同一 engine；`scripts/stress_sqlite_writes.py:159` 同样 `asyncio.gather`。
  - 缓解只有 `busy_timeout=30s`（`app/database.py:152`）；`app/database.py:106-108` 记录仍会失败的情形，根治需 `BEGIN IMMEDIATE`，未做。
  - 同源默认配置代价：2 worker 下进程内 APScheduler 每个 worker 各起一份，定时爬取 / 日报重复触发（`docker-compose.yml:62-68` 注释）。
- **现在怎么讲**：2 worker × SQLite 的跨进程写表现未验证；不要讲「已压测」「已验证」。

### 5. RAG 检索效果：93.8% / 96.4% 已于 2026-11-16 复现

- **实际到哪一步**：关键词 / 语义 / 混合三策略与黄金集（20 知识 / 28 查询）完整。2026-11-16 用本机 `qwen3-embedding:0.6b` 跑 `scripts/eval_rag.py`，两个数字均复现（语义型子集 93.8%、整体 96.4%），与补记一致。**换模型前**的 43.8% / 67.9% 因旧模型未安装，仍只有人背书。
- **证据**：
  - `docs/reports/rag-evaluation.md:26-27`（脚本生成：语义 64.3%、混合 67.9%）、`:34-35`（分类别：keyword 语义型 0.0%、semantic 43.8%、hybrid 43.8%）；生成时间 2026-09-11，旧模型 `nomic-embed-text`。
  - `docs/reports/rag-evaluation-history.md:16-17`（43.8%→93.8%、67.9%→96.4%）、`:25-26`（只有人工背书，无明细 / 表格 / 原始产物）、`:30-58`（本轮复现记录与命令）。
- **现在怎么讲**：93.8% / 96.4% 已于 2026-11-16 复现，可说「脚本可复现」；**换模型前**的 43.8% / 67.9% 仍只有人背书。主报告 `rag-evaluation.md` 里可复现的那组数字对应旧模型，新模型的复现记录在 `rag-evaluation-history.md:30-58`。

### 6. 详情回填：工具可用，HTML 快照路径拿不到

- **实际到哪一步**：回填脚本与抓取器可用，只更新三列。2026-11-16 夜在真实站点跑过 332 条：
  完整正文 **16/555 → 270/555**，`detail_html` 仍 **0/555**。唯一能拿到数据的路径是搜索接口
  （返回 JSON 记录，无整页 HTML）；详情页未登录态打不开。成功率 80.1%，失败 93.9% 是 WAF。
- **证据**：
  - `scripts/backfill_detail.py:2,:13` 只 UPDATE `job_desc` / `detail_html` / `publish_time`；`:234-245` 实际写入语句。
  - `scripts/backfill_detail.py:134-149` 注释：2026-11-12 实测搜索接口无整页 HTML，回填 52 条后 `detail_html` 仍 0/555；详情页缺口属另一条取数路径。
  - 快照命令：`select count(*) from job where detail_html is not null and detail_html<>'';` → 0；`select count(*),min(length(job_desc)),max(length(job_desc)) from job where length(job_desc)>200;` → 16，223–1482。
  - 回填不重算 `skills`：16 条完整正文中 15 条库内 `skills` 与 `extract_skills(title, job_desc)` 结果不一致（`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §4.4）；脚本只改三列（`backfill_detail.py:234-245`）。
- **现在怎么讲**：回填链路能补正文；`detail_html` 快照目前拿不到；描述带来的技能增量尚未进入库内技能列。操作步骤与准入条件见 `docs/backfill-playbook.md`。

### 7. 回填止损：已修，离线 stub 实测可触发；真站全库未跑

- **实际到哪一步**：`--max-consecutive-blocked` 止损参数在登录墙稳态下原本结构性不可达；现已在 latch 置位后唯一还会走的搜索兜底路径上补了两层结构化风控判据，连续被拦计数能涨到阈值并真的停批。修复只在打桩传输层 + 单测上验证过，没有对真实站点跑全库。
- **修复点**（`app/crawlers/detail_fetcher.py`）：
  - `_collect` 全量收下接口响应（含 WAF / 登录页 HTML），不再只收 JSON 形态（`:709-721`）。
  - 搜索页页面层判一次（URL / title / 骨架 / 403·429，`:764-771`；判为 WAF 时 `mark_blocked()`，`:769-770`）。
  - 接口响应再判一次（`:774-780`；WAF 时 `mark_blocked()`，`:778-779`）。
  - 阈值语义：`BLOCKED_KINDS = (waf, login_wall)`（`:104-117`）；脚本只在最终分类属于 blocked 类别时累加连续计数，成功 / network / parse / no_field 都清零（`scripts/backfill_detail.py:491`，停批 `:509-517`）。
- **实测证据**：`deliveries/archive-20261116c/codebuddy-REPORT-第8轮.md` §2.1 —— 阈值设 3，第 3 条停批、退出码 4、静态详情请求仅 1 次、分类 1 条 login_wall + 2 条 waf；§2.3 负向用例：`no_field` 连打 8 条不停、退出码 0。全量 1177 passed / 0 failed（该报告 §2.4）。
- **未覆盖**：同报告 §三.1 自陈网络出口是替身（登录页 + WAF 页形状取自 2026-11-12 实测样本），未对 51job 真站跑全库；真站 WAF 形态若改版，判据需跟着加新标记。
- **现在怎么讲**：止损在离线 stub 与单测下可触发；不要讲「真站全库已验证止损」。

### 8. 技能抽取与推荐链：词表字面命中，样本小

- **实际到哪一步**：
  - 抽取是固定词表（66 个去重技术词，`cleaner.py:7-18`）字面命中，单条最多 8 个标签，不做语义理解；词表不含软技能与大量工具。
  - 推荐候选池取 NEW + ACTIVE，共 555 条；过滤只 LIKE `skills` / `title`，不读 `job_desc`。
  - 16 条完整正文的 A/B 对照（仅标题 0.38 个/条 → 标题+描述 4.25 个/条）是小样本、非随机抽样，且该收益未回写库内 `skills` 列。
  - **2026-11-16 夜在 270 条完整正文上重跑同一对照，增益远大于 16 条小样本**：
    仅标题 **2.57 个/条**、标题+正文 **29.78 个/条**（增益 7348 个标签）。
    但库内 `skills` 列**未同步**：270 条里只有 15 条与 `extract_skills(title, job_desc)` 结果完全一致。
    即：正文已具备，技能收益尚未落库——这是当前最值得做的一次性补算。
- **证据**：
  - `app/crawlers/cleaner.py:7-18` 词表；`:144` `extract_skills`；`:150` 匹配文本 = 标题 + 描述；`:163` 上限 8 个。
  - `app/services/job_service.py:31` `VISIBLE_STATUSES = ("NEW", "ACTIVE")`；`app/recommender/analyzer.py:129`（候选池 `in_(["NEW","ACTIVE"])`）、`:134-142`（按 `skills`/`title` LIKE 过滤）、`:156`（技能相似度权重 0.7 / 学历 0.2 / 经验 0.1）。
  - `app/services/skill_profile.py:20,:91`（画像取 `VISIBLE_STATUSES`）、`:101-103`（无 `skills` 直接跳过）。
  - 快照命令：在架 42、有标签 13（`select count(*) from job where job_status='ACTIVE' and skills is not null and skills<>'';`）；在架技能去重仅 Python 8 / Java 4 / 数据分析 2。
  - 16 条 A/B 对照与样本局限：`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §2、§5。
- **现在怎么讲**：抽取是词表命中；推荐 / 画像结论建立在 13 条有标签的在架岗位上，用于演示链路，不代表市场；不要讲「基于岗位正文推荐」「自动理解岗位要求」。

### 9. 薪资预测：规则系数估算，不是模型预测

- **实际到哪一步**：`predict_salary` 是纯函数，城市 / 经验 / 学历系数与高薪技能清单硬编码，输入全部来自请求参数，不读岗位库。
- **证据**：`app/recommender/salary_predictor.py:1-30`（三组系数表 + 基准薪资硬编码）、`:63` 起（预测纯函数）。
- **现在怎么讲**：对外说是「基于经验系数的估算」，不要讲「模型预测」。

### 10. 声明式规格引擎：JSON 抽取层可用，真实站点未跨过 WAF

- **实际到哪一步**：引擎支持 HTML、CSS 分支与 JSON 路径表达式；`type: api` 明确禁止 `detail.*` 配置（拒绝而非静默忽略）。已登记平台为 boss / 51job / quotes_demo；`_job51.yaml` 是下划线开头草稿，加载时跳过。51job 搜索接口对非浏览器客户端返回阿里云 WAF HTML，拿不到 JSON；JSON 源目前只在公开示例（jobicy）上验证过抽取层，示例不入库且城市 / 薪资字段口径有误。
- **证据**：
  - `app/crawlers/spec_schema.py:361-365`（api + detail 抛 `SpecError`）；`:505-506`（下划线开头跳过）。
  - `app/crawlers/registry.py:21-22`（boss / 51job 注册表）、`:52`（`SUPPORTED_PLATFORMS` = 注册表 ∪ SPECS）；`app/crawlers/specs/` 下仅 `quotes_demo.yaml` 生效，`_job51.yaml` 禁用。
  - WAF 实测与示例口径：`deliveries/archive-20261112/qoder-REPORT-第4轮.md` §三.4、G8 / D6。
- **现在怎么讲**：声明式接入降低了加源成本；真实招聘站的 JSON 接入尚未打通。

### 11. 看板与图表：四处展示缺陷已修；模型页残留一处口径不一致

四处都是「用户一打开首页 / 分析页就能看到的错误数字」，合并后代码现状逐条核对如下：

- **「平均薪资」卡片恒 0 → 已修**：薪资区间接口只返回 `name` / `count`，从不产出 `avgSalary`，旧前端按这个不存在的键加权，结果永远是 0。后端 `stat_summary` 现算 `avg_salary`（仅可见岗位口径，`app/services/job_service.py:451-455,:474`）；前端改读 `summary.avg_salary`（`frontend/src/views/Dashboard.vue:240`），旧加权写法已移除（注释 `:233-237`）。薪资区间接口仍只返回 `name` / `count`（`job_service.py:364-391`，`:391`）。
- **「企业数量」卡片恒 ≤20 → 已修**：旧卡片取 `stat/company`（Top-20 榜单）的数组长度。后端 `stat_summary` 新增 `company_count`（`count(distinct company_name)`，限定可见岗位口径，`job_service.py:460-464,:476`）；前端改读该字段（`Dashboard.vue:239`），旧数组长度写法已移除（注释 `:233-235`）。企业榜单接口仍 `limit(20)`（`job_service.py:335-342`，`:339`）。
- **分析摘要 `[object Object]` → 已修**：`/analysis/summary` 返回结构化对象，旧前端直接把对象塞进 `:title`。`Analysis.vue:191-194` 已显式把 summary 拼成可读句子（累计 / 在架 / 平均薪资 / 企业数 / 更新时间）；`:16` 的 `:title` 绑定该字符串 ref（`:163`）。
- **DataCompare 薪资列与卡片恒 0 → 已修**：该页数据源是状态 / 城市 / 学历 / 经验分组，后端只返回 name/count。表格列只剩 name / count / percentage / 趋势（`frontend/src/views/DataCompare.vue:89-100`）；旧的 `avgSalary` 列与两个薪资卡片已移除，改为展示真实可得的总数量 / 分组数（注释 `:139-141`，statItems `:142-144`）。
- **残留口径不一致**：`skill_profile.py:20,:91` 已改用 `VISIBLE_STATUSES`；`local_model_service.py` 薪资分析用可见口径（`:46-48`），岗位状态按 ACTIVE / NEW / OFFLINE 拆分计数求和（`:62,:67,:72`）；但技能分析（`:91`）、城市分析（`:113`）、学历分析（`:127`）仍硬编码 `"ACTIVE"`，只数 42 条在架，与可见口径 555 不同。

### 12. 回填脚本其余已知审计项

| 编号 | 现状 |
|---|---|
| F2 | 已修：静态 403 / 429 判为 `waf`（计入止损），5xx 归 `network`（不计）；搜索兜底保护集合含 `waf / login_wall / network`，不再把已判明的类别降级成 `parse` / `no_field`（`app/crawlers/detail_fetcher.py:167-168,:560,:598,:627,:631`） |
| F3 | 部分修：状态文件损坏 / 顶层非对象 → `StateFileError`，退出码 7，原文件一字不动（`scripts/backfill_detail.py:267-273,:390-395`）；状态文件记录所属库，跨库 resume 拒绝（`:274-279`，写 `db` 字段 `:397`）；状态文件 `.tmp` + `os.replace` 原子写（`:285-295`），旧格式（无 `db`）仍可读。未做：人工备份回滚后的 `ok` 行不补齐——脚本无 restore 入口（`deliveries/archive-20261116c/trae-REPORT-第8轮.md` §六.2） |
| F4 | 已修：备份改 `sqlite3.Connection.backup()` 在线接口（`backfill_detail.py:205`）；副本上跑 `PRAGMA integrity_check`，非 `ok` 抛 `BackupError`（`:206-208`）；生命周期：校验通过保留，校验不过 / 复制异常立刻删半截文件并中止，退出码 5（`:186-190,:218-220,:427-432`）；路径 `<库名>.bak-<时间戳>`，同秒碰撞加序号（`:194-198`） |
| F5 | 已修：脚本连接 `timeout=30` + 显式 `PRAGMA busy_timeout=30000`，与 ORM 30 秒对齐（`backfill_detail.py:120-123,:152-158`）；等满 30 秒拿不到写锁 = 明确失败不重试，退出码 6（`:539-547`）；批量层接 `OperationalError` / 其他 Exception，先 rollback 本条未提交写入（`:539-552`）；汇总在 `finally` 必出（`:553-563`）；覆盖率查询单独 try/except（`:568-578`）；`main()` 三层兜底：`KeyboardInterrupt`→130、`sqlite3.Error`→6/1、其他→1（`:646-663`） |
| F7 | 已修：静态路径调风控判据时传 `<title>`（`_html_title` `app/crawlers/detail_fetcher.py:153`；调用点 `:548-549`），与渲染路径一致 |
| F8 | 部分前移：红线校验挪到每条 commit 之前，本条可随事务回滚（`_verify_one_status` `backfill_detail.py:298-313`，调用 `:466-471`）；批次结束后保留一次全量复核（`:600-607`）。退出码 3 时之前已提交的行仍无法自动撤销（`:601-604`，`deliveries/archive-20261116c/trae-REPORT-第8轮.md` §六.4） |
| F9 | 部分缓解：F1 核心路径用真 `fetch()` + 真 `run()`、仅打桩网络出口（`tests/test_detail_fetcher.py:1183-1360`）；其余批量用例仍建 8 列小表（`:388-404`）并回放替身结果（`:407-425`） |

### 13. 测试基线与文档契约

- **文档声明**：`docs/showcase.md:28` 写 **1187 项，全部通过**（75 个测试文件）；`:63` 写「1187 项全绿」；`:31` 标注口径为测试数取 `pytest` 汇总行、文件数按 `tests/test_*.py` 计。
- **本工位可静态核对**：`tests/test_*.py` 共 75 个文件；源码内 `def test_` 1033 个、`@pytest.mark.parametrize` 37 处（参数化会展开，读源码数不出总条数）。
- **未本地复跑**：本工位全局 Python 缺 `pytest` / `pydantic` / `fastapi`，`scripts/check_docs.py` 与 `pytest --collect-only` 起不来；1187 项未在本工位重跑，数字以 `showcase.md` 声明与各轮报告口径为准（archive-c codebuddy F1 轮 1177、trae 轮 1180、showcase 最终声明 1187）。

### 14. 回填已可执行，并在真实站点跑过一批

- 三条阻塞项 F1（连续被拦止损）、F4（备份原子化 + 当场校验）、F5（异常不逃逸 + 汇总必出）已修，
  并经离线 stub 用例、单测，以及**真实站点联网回跑**验证（2026-11-16 夜，主库副本上 332 条）。
- **实测结果**（副本 `data/_backfill_run.db`，集成端独立复算一致）：
  - 332 条中成功 266 / 失败 66（成功率 80.1%）；失败分类 `waf` 62 / `login_wall` 3 / `network` 1
  - 完整正文 **16/555（2.9%）→ 270/555（48.6%）**；`publish_time` 16 → 282
  - 5 条红线 12 个检查点全过：状态分布 `ACTIVE 42 / NEW 513` 未变、薪资为 0 仍 10、行数 555、`job_key` 空 0
  - 13 份备份全部 `integrity_check=ok`、行数 555；单条均值 23.1 秒
  - 统计口径未受影响：`total=555 / avg_salary=16113.85 / company_count=432` 与回填前完全一致
- **未验证**：`[STOP]` 阈值在真实连续拦截下的触发——本轮最长连续被拦 3 条，未达阈值 5，
  所以只证明了「风控计数增长 + 延迟自动拉大」这一级。
- 残留硬边界：`detail_html` 仍 **0/555**，详情页未登录态打不开（见 §6、§三）；
  回填带来的技能增量**未回写**库内 `skills` 列（见 §8）——
  集成端实测：270 条完整正文里，仅 15 条库内 `skills` 与重算结果一致。
- 正式跑之前先读 `docs/backfill-playbook.md`：准入条件、参数表、红线 A1–A5、备份与 resume / 人工介入边界。

## 三、数据侧的硬边界（2026-11-11 快照实测）

> 只读打开 `file:.../recsys.db?mode=ro`；命令可直接复算。
>
> ⚠️ **本节是 11-11 的快照，不是当前值。** 2026-11-16 夜已在副本上回填 332 条，
> 完整正文由 16 变为 270；`detail_html` 仍为 0。当前值请看 §14。
> 保留本节是因为它记录了「未回填时」的基线，可用于对照。

| 指标 | 实测值 | 命令 | 边界 |
|---|---|---|---|
| 岗位总数 / 来源 | 555，全部 `51job` | `select count(*) from job;` / `select source_site,count(*) from job group by 1;` | 单一平台、单一批关键词采集 |
| 在架 / 新建 | ACTIVE 42 / NEW 513 | `select job_status,count(*) from job group by 1;` | 推荐候选池、技能画像、分布统计取 NEW + ACTIVE；本地模型页技能 / 城市 / 学历分析仍只数 ACTIVE |
| 完整正文 | 16 条（223–1482 字符）；其余 539 条 ≤200 字符（其中 501 条 ≤20 字符） | `select count(*),min(length(job_desc)),max(length(job_desc)) from job where length(job_desc)>200;` | 16 条非随机抽样，偏 AI/算法方向；不能外推整体抽取效果 |
| 技能标签覆盖 | 全库 190/555 非空；在架 13/42 | `select count(*) from job where skills is not null and skills<>'';` | 365 条全库无标签；画像 / 分布只统计有标签的 |
| `detail_html` | 0/555 | `select count(*) from job where detail_html is not null and detail_html<>'';` | 无本地 HTML 快照可用 |
| `publish_time` | 16/555 | `select count(*) from job where publish_time is not null and publish_time<>'';` | 时间趋势 / 时效性分析不可用 |
| 城市 | 5 个：上海 118 / 长沙 117 / 深圳 112 / 杭州 107 / 北京 101 | `select city,count(*) from job group by 1 order by 2 desc;` | 不能讲「全国城市分布」 |
| 经验写法 | 22 种取值，格式混用（区间 / 及以上 / 单点） | `select count(distinct experience) from job;` | 直接做年限分布会得出假结论 |
| 薪资「面议」 | 10 条 min/max 同为 0 | `select count(*) from job where min_salary=0 and max_salary=0;` | 统计薪资均值 / 最小值时需剔除 0 |
| 学历 | 本科 386 / 硕士 87 / 大专 75 / 高中 2 / 中技/中专 2 / 未填 2 / 初中及以下 1 | `select education,count(*) from job group by 1 order by 2 desc;` | — |
| 公司实体 | 432 | `select count(*) from company;` | — |
| 采集任务 | 53：COMPLETED 44 / FAILED 9 | `select status,count(*) from crawl_task group by 1;` | — |
| 知识库 | 21 条 | `select count(*) from knowledge_base;` | 与岗位库无外键、无查询关系 |
| AI 调用记录 | 63 条 | `select count(*) from ai_usage;` | — |

## 四、明确未做的事（取舍，不是缺陷）

- **薪资预测未做数据训练模型**：系数表硬编码在 `app/recommender/salary_predictor.py:1-30`，定位是规则估算。
- **技能抽取未做语义理解**：封闭词表字面命中（`app/crawlers/cleaner.py:7-18,:163`）；「抽不到」不等于「岗位不需要」。
- **RAG 未接入岗位库**：`app/services/knowledge_service.py` 无 `Job` 导入或查询；问答范围限于知识库 21 条。
- **多 worker 进程内调度重复触发**：compose 默认开启进程内调度，2 worker 下每个 worker 各起一份；compose 注释给出的规避方式是生产环境关闭进程内调度（`docker-compose.yml:62-68`）。
- **端到端接口未在审计中起服务验证**：能力矩阵结论来自代码阅读 + 只读 SQL 统计，未启动服务做 `/api/jobs/recommend`、`/skill-profile`、`/stat/*` 前后对照（`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §5.5）。
- **技能抽取无人工标注 ground truth**：只比较命中数量，误报（正文出现某词不等于岗位要求该技能）未评估（同上 §5.2）。
- **回填链路不消耗日配额**：`daily_quota.try_consume` 全仓生产调用仅 `app/services/crawler_service.py:547` 一处，回填脚本不经过该闸门（`deliveries/archive-20261116/qoder-REPORT-第6轮.md` F10，:199-201）。

## 五、2026-11-16 深夜补记（集成端）

以下两项在本文档定稿后合入，补记以免本文档再次落后于代码：

- **`DetailResult.failure` 契约已改为由 `ok` 派生**（`app/crawlers/detail_fetcher.py`）。
  此前静态路径判出登录墙后，渲染或搜索兜底把正文救回来时会留下 `ok=True` 但
  `failure='login_wall'`；现在 `failure` 是属性，成功时结构上必为空串，
  原始判定留在 `_failure` / `attempts` 供排查。入库数据一直是对的，错的是对外口径。
- **CSV / Excel 导入已去掉逐行 SAVEPOINT**（`app/services/export_service.py`）。
  改为 `executemany` + 失败定位后重放其余行：单行写库失败仍不拖垮整批，
  但不再逐行提交，1 万行实测 **32.37s → 0.17s**；计数恒等式
  `success + skip + fail + skipped_empty == total` 在异常路径下仍成立。

测试基线同步 **1187 → 1193**（qoder 4 条契约用例 + codebuddy 2 条导入用例）。
