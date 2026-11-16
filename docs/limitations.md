# 已知边界与验证状态

> 这份文件区分「实现到什么程度」和「验证到什么程度」。每条都能指到代码位置、原始命令或审计报告；没有证据的不写。
> 数据口径来自 2026-11-11 只读快照 `recsys.db`（`mode=ro`）；代码对照只读副本 `repo-readonly`。
> 本文件只记录现状，不含待办计划。

## 一、一览表

| 能力 | 实现 | 验证 | 一句话边界 |
|---|---|---|---|
| 数据库 | 部分 | 仅代码核对 + SQLite 实测 | ORM 与连接串构造支持 SQLite/MySQL/PostgreSQL；实测只跑过 SQLite，MySQL 未验证，PostgreSQL 有一处 `func.date` 不兼容 |
| Redis 跨副本共享（限流 / 配额 / 熔断 / 会话） | 完整（后端切换 + 内存降级接线） | 单测覆盖 Python 替身；真实 Redis 未验证 | 双实例 + 替身对照过机制；真实 Redis 上 Lua 原子性无执行记录 |
| Docker Compose 启动 | 完整编排 | 代码静态核对，未在本工位实跑 | 有前置条件：先配 `.env`、首次构建联网；经 nginx 入口上传实际被卡 1MB |
| 多 worker × SQLite 写 | compose 默认 2 worker | 未验证 | 跨进程写竞争零用例；现有并发实验均为单进程 asyncio |
| RAG 检索效果 | 完整（关键词 / 语义 / 混合三策略，黄金集 20 知识 / 28 查询） | **2026-11-16 已复现**（`qwen3-embedding:0.6b`） | 93.8%（语义型子集）/ 96.4%（整体）已两次独立跑出；**换模型前**的 43.8% / 67.9% 仍只有人背书（旧模型本机未安装） |
| 详情回填 | 工具可用 | 快照实测：完整正文 16/555，`detail_html` 0/555 | 唯一取数路径是搜索接口 JSON，拿不到整页 HTML；详情页未登录态打不开 |
| 回填止损（连续被拦停批） | 有参数 | 已知在登录墙稳态下触发不了，未修 | 止损阈值结构性不可达 |
| 技能抽取 | 完整（封闭词表字面命中） | 16 条小样本定性对照；无标注 ground truth | 不做语义理解；词表外技能抽不到；样本收益未回写库内 `skills` 列 |
| 推荐 / 技能画像 / 技能分布 | 完整 | 只读 SQL + 代码阅读，未起服务端到端验证 | 口径已统一为 NEW + ACTIVE（555 条）；推荐链路不读岗位正文 |
| 薪资预测 | 完整 | 代码静态核对 | 规则系数纯函数，系数硬编码；不是从历史数据训练的模型 |
| 分布统计（城市 / 经验 / 学历 / 薪资） | 完整 | 只读 SQL 实测 | 不依赖岗位正文；分母 555 条；经验已归并成 5 个桶 |
| 看板 / 图表展示 | 完整（前端调用链已逐行核对） | 代码静态核对 + 只读 SQL；未起服务 | 平均薪资卡片恒 0、企业数量卡片恒 ≤20、分析摘要渲染成 `[object Object]`（见 §11） |
| 声明式规格引擎 | 部分（HTML / CSS / JSON 路径） | 抽取层在公开示例上验证过；真实招聘站未打通 | `type: api` 禁止 `detail.*`；已登记 boss / 51job / quotes_demo，51job 搜索接口被 WAF 拦在 HTML |
| RAG 问答 | 完整 | 代码静态核对 | 知识库 21 条，与岗位库独立；不能回答「这条岗位具体要求什么」 |

## 二、逐条说明

### 1. 数据库：支持三库，验证过一种

- **实际到哪一步**：ORM 层是 SQLAlchemy 2.0 异步方言写法，三种连接串构造都在（`app/database.py:52-55` PostgreSQL、`:58-59` SQLite、`:69` MySQL）。实测跑过的只有 SQLite：CI 无数据库服务、测试库强制内存 SQLite；Docker 栈把 `DB_TYPE` 钉在 sqlite。MySQL 路径未验证；PostgreSQL 在 AI 用量日报处有一处已知方言不兼容。
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
  - 生产 Lua：`app/middleware/rate_limit.py:108,:210`；`app/crawlers/throttle.py:66,:368`；`app/utils/circuit_breaker.py:159,:286,:301,:318`。
  - `scripts/verify_redis_docker.py:20-24` 明写 A3/A4（真实 Redis + 2 worker 并发压测）等判据刻意不自动跑，留人工清单。
- **现在怎么讲**：跨副本共享的接线与降级契约已实现并经双实例对照；真实 Redis 上的 Lua 原子性、真双进程并发组合未验证。

### 3. Docker Compose：有前置条件，不是开箱即起

- **实际到哪一步**：compose 编排了后端 / 前端 / Redis 三个服务；启动有前置条件，且入口体积上限存在已知残缺。
- **证据**：
  - 先准备根目录 `.env`（模板 `.env.docker.example`）；`JWT_SECRET` 不配会故意起不来（fail-fast），长度不足 32 字符生产环境拒绝启动：`docker-compose.yml:32-36`、`app/config.py:576,:595-596`。
  - 首次构建联网下载 npm / pip / Playwright chromium；镜像源参数见 `docker-compose.yml:18-22` 注释。
  - `docker/nginx.conf` 全文无 `client_max_body_size`，nginx 默认 1MB 生效；应用侧导入上限 `app/services/export_service.py:31` `IMPORT_MAX_BYTES = 10MB`；对外唯一端口 `8080:80`（`docker-compose.yml:96`），经入口上传 >1MB 文件在 nginx 层 413。
- **现在怎么讲**：能起，但要同时说明 `.env` 前置、首次联网、入口 1MB 上限；不要讲「一键启动」。

### 4. 多 worker × SQLite：跨进程写竞争未验证

- **实际到哪一步**：compose 默认 2 worker 共享一个 SQLite 文件（`docker-compose.yml:27` `--workers ${BACKEND_WORKERS:-2}`、`:30` sqlite）。仓库内并发写用例全部是单进程 asyncio；跨进程写锁路径零用例。
- **证据**：
  - `tests/` 目录 grep `workers|multiprocessing|Process(|Popen|fork` 0 命中；唯一并发写用例 `tests/test_crawler_validation.py:1030` 8 个协程共用同一 engine；`scripts/stress_sqlite_writes.py:159` 同样 `asyncio.gather`。
  - 缓解只有 `busy_timeout=30s`（`app/database.py:115`）；`app/database.py:105-111` 记录仍会失败的情形，根治需 `BEGIN IMMEDIATE`，未做。
  - 同源默认配置代价：2 worker 下进程内 APScheduler 每个 worker 各起一份，定时爬取 / 日报重复触发（`docker-compose.yml:62-68` 注释）。
- **现在怎么讲**：2 worker × SQLite 的跨进程写表现未验证；不要讲「已压测」「已验证」。

### 5. RAG 检索效果：93.8% / 96.4% 已于 2026-11-16 复现

- **实际到哪一步**：关键词 / 语义 / 混合三策略与黄金集（20 知识 / 28 查询）完整。2026-11-16 用本机 `qwen3-embedding:0.6b` 跑 `scripts/eval_rag.py`，两个数字均复现（语义型子集 93.8%、整体 96.4%），集成端独立重跑结果逐项一致。**换模型前**的 43.8% / 67.9% 因旧模型未安装，仍只有人背书。
- **证据**：
  - `docs/reports/rag-evaluation.md:25-27`（脚本生成：语义 64.3%、混合 67.9%）、`:33-35`（分类别：keyword 语义型 0.0%、semantic 43.8%、hybrid 43.8%）；生成时间 2026-09-11，旧模型 `nomic-embed-text`。
  - `docs/reports/rag-evaluation-history.md:16-17`（43.8%→93.8%、67.9%→96.4%）、`:24-28`（只有人工背书，无明细 / 表格 / 原始产物）、`:37-38`（本轮未重跑，不能用「已复现」描述）。
- **现在怎么讲**：93.8% / 96.4% 已于 2026-11-16 两次独立跑出，可说「脚本可复现」；**换模型前**的 43.8% / 67.9% 仍只有人背书。主报告 `rag-evaluation.md` 里可复现的那组数字对应旧模型，新模型的复现记录在 `rag-evaluation-history.md:30-58`。

### 6. 详情回填：工具可用，HTML 快照路径拿不到

- **实际到哪一步**：回填脚本与抓取器可用，只更新三列；快照中完整正文 16/555，`detail_html` 0/555。唯一能拿到数据的路径是搜索接口（返回 JSON 记录，无整页 HTML）；详情页未登录态打不开。
- **证据**：
  - `scripts/backfill_detail.py:2,:13` 只 UPDATE `job_desc` / `detail_html` / `publish_time`；`:149-153` 实际写入语句。
  - `scripts/backfill_detail.py:95-103` 注释：2026-11-12 实测搜索接口无整页 HTML，回填 52 条后 `detail_html` 仍 0/555；详情页缺口属另一条取数路径。
  - 快照命令：`select count(*) from job where detail_html is not null and detail_html<>'';` → 0；`select count(*),min(length(job_desc)),max(length(job_desc)) from job where length(job_desc)>200;` → 16，223–1482。
  - 回填不重算 `skills`：16 条完整正文中 15 条库内 `skills` 与 `extract_skills(title, job_desc)` 结果不一致（`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §4.4）；脚本只改三列（`backfill_detail.py:149-153`）。
- **现在怎么讲**：回填链路能补正文；`detail_html` 快照目前拿不到；描述带来的技能增量尚未进入库内技能列。

### 7. 回填脚本止损：登录墙稳态下不可达（未修）

- **实际到哪一步**：脚本有 `--max-consecutive-blocked` 止损参数；实测在「第 1 条登录墙后置位 latch、后续全走搜索兜底」的稳态下，止损判据结构性不可达，熔断一次也不会开。
- **证据**：`deliveries/qoder-REPORT.md` F1（真 `fetch()` + 真 `run()` 离线稳定复现，连续被拦最大长度实测 = 1）；代码位置 `app/crawlers/detail_fetcher.py:513`（latch 置位后跳过前两步）、`:588`（剩余搜索兜底路径）、`:664`（`_search_by_keyword`）。
- **现在怎么讲**：止损参数存在，但登录墙稳态下触发不了；这是已知未修项。

### 8. 技能抽取与推荐链：词表字面命中，样本小

- **实际到哪一步**：
  - 抽取是固定词表（66 个去重技术词，`cleaner.py:7-14`）字面命中，单条最多 8 个标签，不做语义理解；词表不含软技能与大量工具。
  - 推荐候选池取 NEW + ACTIVE，共 555 条；过滤只 LIKE `skills` / `title`，不读 `job_desc`。
  - 16 条完整正文的 A/B 对照（仅标题 0.38 个/条 → 标题+描述 4.25 个/条）是小样本、非随机抽样，且该收益未回写库内 `skills` 列。
- **证据**：
  - `app/crawlers/cleaner.py:7-14` 词表；`:144` `extract_skills`；`:150` 匹配文本 = 标题 + 描述；`:163` 上限 8 个。
  - `app/services/job_service.py:24` `VISIBLE_STATUS = "ACTIVE"`；`app/recommender/analyzer.py:129`（候选池 `in_(["NEW","ACTIVE"])`）、`:134-142`（按 `skills`/`title` LIKE 过滤）、`:156`（技能相似度权重 0.7 / 学历 0.2 / 经验 0.1）。
  - `app/services/skill_profile.py:90`（只取 ACTIVE）、`:100-102`（无 `skills` 直接跳过）。
  - 快照命令：在架 42、有标签 13（`select count(*) from job where job_status='ACTIVE' and skills is not null and skills<>'';`）；在架技能去重仅 Python 8 / Java 4 / 数据分析 2。
  - 16 条 A/B 对照与样本局限：`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §2、§5。
- **现在怎么讲**：抽取是词表命中；推荐 / 画像结论建立在 13 条有标签的在架岗位上，用于演示链路，不代表市场；不要讲「基于岗位正文推荐」「自动理解岗位要求」。

### 9. 薪资预测：规则系数估算，不是模型预测

- **实际到哪一步**：`predict_salary` 是纯函数，城市 / 经验 / 学历系数与高薪技能清单硬编码，输入全部来自请求参数，不读岗位库。
- **证据**：`app/recommender/salary_predictor.py:1-31`（三组系数表 + 基准薪资硬编码）、`:63-88`（预测纯函数）。
- **现在怎么讲**：对外说是「基于经验系数的估算」，不要讲「模型预测」。

### 10. 声明式规格引擎：JSON 抽取层可用，真实站点未跨过 WAF

- **实际到哪一步**：引擎支持 HTML、CSS 分支与 JSON 路径表达式；`type: api` 明确禁止 `detail.*` 配置（拒绝而非静默忽略）。已登记平台为 boss / 51job / quotes_demo；`_job51.yaml` 是下划线开头草稿，加载时跳过。51job 搜索接口对非浏览器客户端返回阿里云 WAF HTML，拿不到 JSON；JSON 源目前只在公开示例（jobicy）上验证过抽取层，示例不入库且城市 / 薪资字段口径有误。
- **证据**：
  - `app/crawlers/spec_schema.py:361-367`（api + detail 抛 `SpecError`）；`:506-507`（下划线开头跳过）。
  - `app/crawlers/registry.py:20-22`（boss / 51job 注册表）、`:52`（`SUPPORTED_PLATFORMS` = 注册表 ∪ SPECS）；`app/crawlers/specs/` 下仅 `quotes_demo.yaml` 生效，`_job51.yaml` 禁用。
  - WAF 实测与示例口径：`deliveries/archive-20261112/qoder-REPORT-第4轮.md` §三.4、G8 / D6。
- **现在怎么讲**：声明式接入降低了加源成本；真实招聘站的 JSON 接入尚未打通。

### 11. 看板与图表：三处展示缺陷已于 2026-11-16 修复

三处都是「用户一打开首页/分析页就能看到的错误数字」，起真实服务 + 探针库实测确认后修复：

- **「平均薪资」卡片恒 0 → 已修**：薪资区间接口只返回 `name` / `count`，从不产出 `avgSalary`，而 `Dashboard.vue` 按这个不存在的键加权，结果永远是 0。改为直接读 `/analysis/summary` 已算好的 `avg_salary`（真实库实测 16113.85），前端不再重复计算。
- **「企业数量」卡片恒 ≤20 → 已修**：卡片取 `stat/company`（Top-20 榜单）的数组长度。后端 `stat_summary` 新增 `company_count`（`count(distinct company_name)`，限定可见岗位口径），前端改读该字段。
- **分析摘要 `[object Object]` → 已修**：`/analysis/summary` 返回对象，`job.ts` 却声明 `Promise<string>`，`Analysis.vue` 直接把对象塞进 `:title`。收紧类型为 `AnalysisSummary`，并在前端拼成可读句子。
- 同类第四处：`DataCompare.vue` 的「平均薪资 / 最高薪资」列与卡片也读不存在的 `avgSalary`（数据源 status/city/education/experience 只有 name/count），恒为 0。已移除这两列和两个薪资卡片，改为展示真实可得的「分组数」——不造假数据。

### 12. 回填脚本其余已知审计项（未修，详见 `deliveries/qoder-REPORT.md`）

| 编号 | 现状 |
|---|---|
| F2 | 静态路径已判明的 403 / 5xx 被搜索兜底降级为 parse / no_field，不计入止损（`app/crawlers/detail_fetcher.py:601-602`） |
| F3 | 状态文件无对账：备份回滚后的 `ok` 行不补；半截 JSON 被当空文件抹掉已有记录；不记录所属库 |
| F4 | 备份为 `shutil.copy2` 整库镜像，非原子；边写边拷实测 0.28% / 0.54% 副本 `integrity_check` 不通过，脚本不校验、不清理 |
| F5 | 并发写锁按 sqlite3 默认 5 秒超时，`OperationalError` 直接逃逸；备份已生成、状态文件半截时，汇总 / 覆盖率 / 红线校验不执行 |
| F6 | 搜索响应 5000 字节闸门：需 ≥7 条 item 才过闸；真实返回条数分布未测 |
| F7 | 静态路径调风控判据时不传 `<title>`（`app/crawlers/detail_fetcher.py:527`），渲染路径传 |
| F8 | `job_status` 红线校验是探测器不是护栏：`exit=3` 时已 commit 的行不回滚；告警走未配置的 logger |
| F9 | 测试桩过深：回填用例建 8 列小表（真表 22 列）；止损用例替身每条返回 login_wall，真 `fetch()` 在 latch 后产不出该形状 |

## 三、数据侧的硬边界（2026-11-11 快照实测）

> 只读打开 `file:.../recsys.db?mode=ro`；命令可直接复算。

| 指标 | 实测值 | 命令 | 边界 |
|---|---|---|---|
| 岗位总数 / 来源 | 555，全部 `51job` | `select count(*) from job;` / `select source_site,count(*) from job group by 1;` | 单一平台、单一批关键词采集 |
| 在架 / 新建 | ACTIVE 42 / NEW 513 | `select job_status,count(*) from job group by 1;` | `job_service` 各统计、技能画像、模型页按 ACTIVE；推荐候选池取 NEW + ACTIVE |
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

- **薪资预测未做数据训练模型**：系数表硬编码在 `app/recommender/salary_predictor.py:1-31`，定位是规则估算。
- **技能抽取未做语义理解**：封闭词表字面命中（`app/crawlers/cleaner.py:7-14,:161`）；「抽不到」不等于「岗位不需要」。
- **RAG 未接入岗位库**：`app/services/knowledge_service.py` 无 `Job` 导入或查询；问答范围限于知识库 21 条。
- **多 worker 进程内调度重复触发**：compose 默认开启进程内调度，2 worker 下每个 worker 各起一份；compose 注释给出的规避方式是生产环境关闭进程内调度（`docker-compose.yml:62-68`）。
- **端到端接口未在审计中起服务验证**：能力矩阵结论来自代码阅读 + 只读 SQL 统计，未启动服务做 `/api/jobs/recommend`、`/skill-profile`、`/stat/*` 前后对照（`deliveries/archive-20261113/trae-REPORT-第4轮内容-第5轮未交付.md` §5.5）。
- **技能抽取无人工标注 ground truth**：只比较命中数量，误报（正文出现某词不等于岗位要求该技能）未评估（同上 §5.2）。
- **回填链路不消耗日配额**：`daily_quota.try_consume` 全仓仅 `app/services/crawler_service.py:547` 一处调用，回填脚本不经过该闸门（`deliveries/qoder-REPORT.md` F10）。
