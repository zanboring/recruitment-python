# 数据来源与口径说明（data-provenance）

> 目的：回答「库里的这些数据从哪来、可不可信、怎么核验」。
> 所有结论均以代码现状为准；查不到的地方标注「未确认」。

---

## 1. `job.source_site` 的全部取值

`job.source_site` 是区分数据来源的唯一字段。代码里允许出现的取值如下：

| `source_site` | 含义 | 写入路径 | 出处 |
|---|---|---|---|
| `boss` | BOSS 直聘（服务端 Playwright 爬虫，或浏览器油猴上报） | 服务端爬虫：`app\crawlers\boss.py:302`；浏览器上报走 `app\services\crawler_service.py:439` 的清洗后入库 | `app\services\crawler_service.py:54`（PLATFORM_META） |
| `51job` | 前程无忧（服务端爬虫，或浏览器油猴上报） | `app\crawlers\job51.py:356` | `app\services\crawler_service.py:56` |
| `zhaopin` | 智联招聘（**仅浏览器油猴上报**，服务端没有对应爬虫） | 油猴脚本提交 → `POST /api/crawler/ingest` | `app\services\crawler_service.py:55` |
| `liepin` | 猎聘（**仅浏览器油猴上报**，服务端没有对应爬虫） | 同上 | `app\services\crawler_service.py:57` |
| `seed` | **演示（合成）数据**，不是真实岗位 | `scripts\seed_demo_data.py:102` | `app\services\crawler_service.py:98`（DEMO_SOURCE_SITE = "seed"） |
| `vision` | 视觉识别导入（招聘截图 → GLM-4V 结构化） | `POST /api/jobs/vision-import` / `vision-batch-import`，服务端写死 `source_site="vision"` | `app\routers\jobs.py:288,355`；`app\services\vision_service.py:128` |
| `url` | 链接导入（粘贴招聘详情页 URL，Playwright 打开页面抽取） | `POST /api/jobs/url-import` | `app\services\url_import_service.py:307,312` |
| `import` | CSV / Excel 批量导入；文件里「来源平台」列为空时落此值 | `POST /api/jobs/import` | `app\services\export_service.py:137,211` |
| `quotes_demo` | 声明式示例数据源（quotes.toscrape.com），**只产出分析用行，不写岗位表** | `app\crawlers\specs\quotes_demo.yaml:15`，`mapping.target: generic` | `app\crawlers\registry.py:52` |

### 一个必须知道的口径差异

入库有**两条不同的写入路径**，校验强度不同：

1. **服务端爬虫 + 浏览器上报**（`crawler_service.save_job`）：入库前强制过 `normalize_source_site()` 白名单校验，
   来源为空或未知直接拒入（`app\services\crawler_service.py:112-130, 349-356`）。
2. **管理端手动入库**（`JobService.create_job`：视觉 / 链接 / CSV 导入 / 管理端新增）：
   直接构造 `Job` 对象，**不经过上面的白名单校验**（`app\services\job_service.py:56-73`）。

因此 `vision` / `url` / `import` 这三个取值**不在** `crawler_service.VALID_SOURCE_SITES` 白名单里，
但它们会出现在 `job` 表中——这是代码现状，不是数据异常。

---

## 2. 每条通道的前置条件

| 通道 | 需要登录？ | 需要密钥？ | 需要人工操作？ | 出处 |
|---|---|---|---|---|
| 服务端爬虫 `boss` | 需要 BOSS 登录态文件 `data/boss_storage_state.json`（用 `scripts\save_boss_login.py` 生成） | 否 | 首次扫码登录；之后按关键词 × 城市自动跑 | `app\config.py:366`（`crawl_storage_state`） |
| 服务端爬虫 `51job` | 需要 | 否 | 同上 | 同上 |
| 浏览器油猴上报（`boss`/`51job`/`zhaopin`/`liepin`） | 需要——用户在自己浏览器里登录各招聘站 | 需要在系统侧配置 `BROWSER_COLLECT_TOKEN`（油猴脚本里要填同一个值） | 是：人工打开招聘页、点「采集本页」 | `app\routers\crawler.py:122-152`；`userscript\recsys-collector.user.js:55-58` |
| 视觉识别导入 `vision` | 需要管理员登录系统 | 需要 `ZHIPUAI_API_KEY` | 是：人工准备招聘截图、上传、预览确认后入库 | `app\services\vision_service.py:138-142`；`app\routers\jobs.py:252` |
| 链接导入 `url` | 需要管理员登录系统 | 无强制（有 LLM 时抽取更准，无则走正则粗提） | 是：人工粘贴 URL | `app\services\url_import_service.py:203-218` |
| CSV / Excel 导入 `import` | 需要管理员登录系统 | 否 | 是：人工准备结构化文件（列头：标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述） | `app\routers\jobs.py:443-464` |
| 演示数据 `seed` | 无 | 无 | 跑 `scripts\seed_demo_data.py` | `scripts\seed_demo_data.py:9` |

**注意**：`seed` 是合成数据。一旦与真实行混在同一张表里，按来源统计的图表会把两者混算，
必须靠 `source_site` 字段区分。项目自带只读统计脚本 `scripts\source_stats.py` 把两者分开：

```bat
.venv\Scripts\python.exe scripts\source_stats.py
```

输出形如：来源分布（`job.source_site → 岗位数`），并汇总 `真实 / 演示 / 来源不明` 三档。
（`scripts\source_stats.py:1-19`）

---

## 3. 怎么核验一条岗位是真的

### 3.1 用 SQL 看某条数据的来源与时间

```sql
-- 查全部来源分布
SELECT source_site, COUNT(*) FROM job GROUP BY source_site;

-- 查某一条岗位的来源、录入时间、最后见到时间
SELECT id, title, company_name, source_site, job_status,
       url, created_at, last_seen_at, last_checked_at
FROM job
WHERE id = <岗位ID>;
```

在命令行里跑（用项目 venv 的 sqlite3 模块）：

```bat
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/recsys.db'); [print(r) for r in c.execute('select id, title, company_name, source_site, job_status, created_at from job order by id desc limit 20').fetchall()]"
```

### 3.2 判断「真」与「演示」的口径

代码里 `count_jobs_by_source()` 的定义（`app\services\crawler_service.py:133-160`）：

| 分类 | 判定 |
|---|---|
| `demo`（演示） | `source_site = 'seed'` |
| `real`（真实） | `source_site` 非空 且 不是 `seed` |
| `unknown`（异常） | `source_site` 为空 |

### 3.3 核验一条具体岗位「是否真实存在」

- `url` 字段（`app\models\job.py:25`）：浏览器上报和视觉导入的行，`url` 可能为空；
  服务端爬虫和链接导入的行有原始链接，点开可对照岗位标题、公司、城市。
- `last_checked_at` / `last_seen_at`：岗位存活核查（`job_checker`）会周期性刷新。
  `job_status` 取值：`NEW` / `ACTIVE` / `OFFLINE`（`app\models\job.py:16`；`app\services\crawler_service.py:383,607`）。
- `job_key`：岗位指纹，格式 `<source_site>_<摘要>`（`app\utils\job_key.py:37`），
  同一条岗位重复入库会命中唯一约束被去重。

### 3.4 浏览器上报的可追溯性

油猴脚本采集时会带上页面 `url`（`app\services\crawler_service.py:448`），
所以 `source_site in ('boss','51job','zhaopin','liepin')` 且 `url` 非空的行，
可以直接打开链接核对。

---

## 4. 薪资 / 城市 / 经验字段是怎么归一化的

### 4.1 薪资（`min_salary` / `max_salary`，单位：元/月）

唯一实现：`app\utils\salary.py` 的 `parse_salary_to_monthly()`（第 56 行）。
各写入路径都收敛到它（`cleaner.py:139-141` 只是薄封装）。

单位倍率（`app\utils\salary.py:39-45`）：

| 原文单位 | 倍率 |
|---|---|
| `万` / `w` / `W` | ×10000 |
| `千` / `k` / `K` | ×1000 |
| `元` | ×1 |

换算规则：

| 原文 | 归一化结果 |
|---|---|
| `15-25K` | `15000 - 25000` |
| `15-25K·13薪` | `15000 - 25000`（13 薪不折算进月薪） |
| `1-1.5万` | `10000 - 15000` |
| `15-25万/年` | `12500 - 20833`（年包 ÷ 12） |
| `面议` / `保密` / 空 | `0 - 0`（视为缺失） |
| `200-300元/天` | `0 - 0`（非月薪口径，丢弃） |

补充：视觉识别通道的薪资来自模型输出的 `salary_min_k` / `salary_max_k`（单位 K），
入库时 ×1000 转元（`app\services\vision_service.py:114-123`）。

高薪过滤：`min_salary > 30000` 的行在采集阶段会被丢弃（`app\crawlers\cleaner.py:127-128`）。

### 4.2 城市（`city`）

- 入库存的是**城市名文本**（如「长沙」），不是城市编码（`app\models\job.py:17`）。
- 服务端 BOSS 爬虫请求时用 `CITY_CODE_MAP`（`app\crawlers\city_map.py:18-39`，共 20 个城市）
  把城市名映射成 BOSS 的城市编码；未收录城市直接报错、不静默用默认值（`city_map.py:62-78`）。
- 浏览器上报时，城市名以油猴脚本从页面 DOM 取到的原文为准（`app\services\crawler_service.py:442`）。

### 4.3 经验（`experience`）

- 入库存的是**原文**（如「3-5年」「经验不限」），不做数字化归一（`app\models\job.py:18`）。
- 过滤时用 `_experience_lower_bound()` 只取区间**下界**判资深：下界 ≥ 5 年的岗位在采集阶段被丢弃
  （`app\crawlers\cleaner.py:64-97, 100-116`；阈值 `SENIOR_MIN_YEARS = 5`，第 49 行）。

### 4.4 统计图表的口径

所有图表（城市/公司/技能/薪资/学历/经验分布）**只统计 `job_status='ACTIVE'` 的岗位**
（`app\services\job_service.py:24`，`VISIBLE_STATUS = "ACTIVE"`）。
薪资区间分桶按 `(min_salary + max_salary) / 2` 归入 0-5k / 5k-10k / … / 50k+ 七档
（`app\services\job_service.py:244-271`）。

---

## 5. 数据可信度的边界

以下情况统计结果**不可信**，使用前必须知道：

1. **`seed` 演示数据混在表里**：图表默认不过滤来源，会把合成岗位算进分布。
   跑 `scripts\source_stats.py` 看 `demo` 计数；`demo > 0` 时所有图表数字都要打折扣。
2. **管理端手动入口绕过了来源白名单**：视觉 / 链接 / CSV 导入可以写入任意 `source_site`
   （`app\services\job_service.py:56-73`）。如果有人手动导入时填了 `boss` 这种标识，
   这条数据**不能**当作 BOSS 真实采集数据。核验方式：看 `url` 是否非空、`created_at` 是否合理。
3. **薪资为 0 的行**：`面议` / 日薪 / 解析失败的行薪资是 `0-0`。薪资分布图表只统计
   `min_salary IS NOT NULL` 且非零的行，但这些行仍会进入总数。
4. **`zhaopin` / `liepin` 只有人工上报数据**：服务端没有对应爬虫，数据量完全取决于人工采集。
5. **公司实体合并口径**：`company` 表按公司名去重、不区分城市；同名不同公司会被合并
   （`app\services\crawler_service.py:291-332`）。按公司聚合的统计有少量误差是设计内取舍。
6. **下架判定只覆盖服务端爬虫通道**：浏览器上报不触发下架判定（`app\services\crawler_service.py:398-415`），
   即人工上报的岗位不会被自动置为 `OFFLINE`。

---

## 附：我核对过的符号

| 符号 | 出处 |
|---|---|
| `PLATFORM_META`（boss/51job/zhaopin/liepin 映射） | `app\services\crawler_service.py:53-58` |
| `DEMO_SOURCE_SITE = "seed"` | `app\services\crawler_service.py:98` |
| `VALID_SOURCE_SITES` 白名单 | `app\services\crawler_service.py:95` |
| `normalize_source_site` 拒空/拒未知 | `app\services\crawler_service.py:112-130` |
| `count_jobs_by_source` 真实/演示/不明口径 | `app\services\crawler_service.py:133-160` |
| 浏览器上报清洗入库 | `app\services\crawler_service.py:439-453` |
| `vision` 写死在路由 | `app\routers\jobs.py:288,355` |
| `url` 写死在导入服务 | `app\services\url_import_service.py:307,312` |
| `import` 默认来源 | `app\services\export_service.py:137,211` |
| `seed` 写入 | `scripts\seed_demo_data.py:102` |
| `quotes_demo` 声明式源 | `app\crawlers\specs\quotes_demo.yaml:15` |
| `JobService.create_job` 无来源白名单 | `app\services\job_service.py:56-73` |
| `parse_salary_to_monthly` 唯一实现 | `app\utils\salary.py:56` |
| 单位倍率表 | `app\utils\salary.py:39-45` |
| 年包 ÷ 12 | `app\utils\salary.py:106-107` |
| 视觉 K → 元 ×1000 | `app\services\vision_service.py:114-123` |
| `is_high_salary` > 30000 丢弃 | `app\crawlers\cleaner.py:127-128` |
| `CITY_CODE_MAP` 20 城 | `app\crawlers\city_map.py:18-39` |
| `_experience_lower_bound` 只取下界 | `app\crawlers\cleaner.py:64-97` |
| `SENIOR_MIN_YEARS = 5` | `app\crawlers\cleaner.py:49` |
| `VISIBLE_STATUS = "ACTIVE"` | `app\services\job_service.py:24` |
| 薪资分桶区间 | `app\services\job_service.py:244-271` |
| `job_key` 格式 | `app\utils\job_key.py:37` |
| 公司实体按名合并规则 | `app\services\crawler_service.py:291-332` |
| 浏览器上报不下架判定 | `app\services\crawler_service.py:398-415` |
| `BROWSER_COLLECT_TOKEN` 校验 | `app\routers\crawler.py:143-152` |
| `crawl_storage_state` 默认路径 | `app\config.py:366` |
