# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。
**版本号的唯一来源是 `app/version.py` 里的 `APP_VERSION`**，
GitHub Release 的 tag 必须与之一致（`v1.0.0`）—— 更新检查就是靠比对两者判断有没有新版。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> **关于 0.x 条目**：这几条是按 git 提交历史**回溯整理**的里程碑（当时未打 tag），
> 用于说明项目是怎么长起来的；从 `1.0.0` 起，版本号、tag、Release 三者一一对应。

---

## [1.2.0] - 2026-11-12

本轮主线是「真实采集链路补全 + 第二轮安全收口」：规格引擎接上 JSON 接口源，详情回填落地，
审计发现的 SSRF / XSS 路径逐条封死。下列每条均对应具体提交（括号内为短 SHA）。

### 新增

- **声明式规格引擎支持 JSON 接口源**（`type: api` + `json:` 路径表达式）：
  `app/crawlers/spec_schema.py` / `app/crawlers/spec_crawler.py`，样例规格 `app/crawlers/specs/_job51.yaml`，
  说明文档 `docs/data-source-plugin.md`；用例 `tests/test_spec_crawler.py`。（0c78d3d）
- **详情回填链路落地**：`app/crawlers/detail_fetcher.py` 依次走 safe_get 静态 HTML →
  受控 Playwright 渲染 → 51job 搜索接口；`scripts/backfill_detail.py` 只回填
  `job_desc` / `detail_html` / `publish_time`，回填前备份 SQLite，回填后校验 `job_status` 不变；
  用例 `tests/test_detail_fetcher.py`。（a50eda0）
- **51job 接口被拒时改读渲染后 DOM**，新增批量采集器 `scripts/crawl_51job_batch.py`。（ba44e9b）
- **入库时建 / 复用 Company 实体并回填 `company_id`**：`save_job` 作为全部入库路径的汇合点
  统一维护公司实体；`scripts/backfill_company_ids.py` 幂等回填存量空值。（170cd4d）
- **采集治理与审计脚本**：`scripts/check_boss_login.py` 登录态自检、`scripts/source_stats.py`
  来源分布统计、`scripts/audit_api.py` 走真实路由表的只读 API 审计、
  `scripts/audit_empty_db.py` 空库边界对账、`scripts/purge_seed_data.py` 清理合成 seed 岗位
  （默认 dry-run，`--apply` 才删）。（de874ce / f757861 / 9abe969 / 00b6499 / 1b27fe2）
- **空库边界与夹具分布回归**：`tests/test_api_empty_db.py`（空库下统计 / 分页 / 推荐 / 画像 /
  导出 / 日报必须返回合法空值，不得 500）、`tests/test_api_job_distribution.py`（20 条固定夹具
  的城市 / 技能 / 薪资 / 学历 / 经验手算对账）。（00b6499）
- **仓库健康自检** `scripts/repo_health.py`：工位脏 / 分支残留 / 只读区被改 / 库行数与
  journal_mode 等检查；只读不写，`--strict` 时有 🔴 退出码可用于提交前拦截。（f1950f6）
- 补充 `python -m app.main` 启动入口（`app/main.py` 的 `__main__` 分支）。（2f0c5ef）

### 修复（安全）

- **声明式爬虫 SSRF 收口**：规格爬虫目标 URL 来自第三方页面 href，原先
  `httpx.AsyncClient(follow_redirects=True)` 重定向后才校验。改走 `url_safety.safe_get()`
  （`follow_redirects=False`、每跳先判后发）；Playwright 路径加预检 + document 级 route 守卫；
  `UnsafeURLError` 记为不可重试；内网详情链接停止整批，而非记单条失败继续探测。（53f4b92）
- **回退页存储型 XSS**：`app/static/index.html` 九处插值改用 `esc()` / `escText()`，
  AI 流按纯文本渲染（保留换行，代价是回退页不做 Markdown 排版）。（53f4b92）
- **`route.abort()` 吞掉异常类型**：守卫记录被拒 URL，goto 抛出的 `net::ERR_ABORTED`
  改写回 `UnsafeURLError`；旧测试用替身整段替换抓取路径属假阳性，新测试驱动会 abort 的
  假页面复现真实 abort-then-raise 序列。（056a87a）
- **SSE 换行丢正文**：`data:` 字段按行编码，`split("\n")` 保留尾部空行；回退页按事件解析、
  合并同一事件的多行 data。（056a87a）
- **启动脚本回灌回环绑定**：`start.bat` / `start-generic.bat` 此前仍绑 `0.0.0.0`，
  与 `run_entry.py` 的回环默认值不一致，改为 `127.0.0.1`；非回环地址 + 弱 `JWT_SECRET`
  拒绝启动。（f3cb702 / b06f27a）

### 修复

- **推荐候选池状态口径**：`analyzer.py` 原只取 `ACTIVE`，而爬虫入库写 `NEW`，
  555 条只进 42 条候选；改为 `NEW + ACTIVE`，与 `crawler_service.py` 口径一致，
  `OFFLINE` / `CLOSED` 仍排除。（6762a93）
- **API 源三处缺陷**：
  - D4 风控关键词检测误喂 JSON 正文（合法 JD 含「风控 / 校验 / 审核」会被整页判阻）
    → API 源跳过关键词检测，只认 HTTP 429 与结构失败；
  - D3 `SpecPayloadError` 未进不可重试集合 → 引入 `DeterministicCrawlerError` 标记基类；
  - D5 空数组与全量映射失败两条静默零路径：空数组记 info，全量映射失败抛结构错误并带首条记录的键。（6762a93）
- **导入不复活已下架岗位**：两条契约测试钉住「状态只能由观察驱动」：CLOSED 岗位重导仍
  CLOSED，旧导出不能覆盖库内已更新字段。（3ca56e2）
- **知识库提交后序列化触发 `MissingGreenlet` 500**：序列化前显式 refresh，补 3 条回归。（260b7bd）
- **视觉导入 `validate` 改 query 参数**：multipart 请求无 JSON body，原 `Body(None)` 恒为
  None、入库分支走不到；vision-import 与 vision-batch-import 同源修复，用例
  `tests/test_vision_import_route.py` 钉住契约。（f3cb702）
- **视觉回复 JSON 尾逗号容错**：GLM-4V-Flash 实拍返回带尾逗号与 Markdown 围栏，标准
  `json.loads` 必报错；先标准解析、失败再清洗重试，提示词补约束，含一条实拍原文用例。（74211f7）
- **资深判定按经验区间下界**：旧实现判断字符「5」是否出现，双向误杀 / 漏放；
  `_experience_lower_bound` 按应届 / 上界封顶 / 区间 / 下界敞开 / 单值解析下界，
  年限判据改为下界 ≥ 5。（d95965e）
- **采集任务回滚场景状态不落库**：except 分支在 needs-rollback 会话上 commit 抛
  `PendingRollbackError`，任务永远停在 RUNNING；改为先回滚再写状态，连接不可用时用独立会话兜底。（b06f27a）
- **SQLite 并发写锁冲突**：`busy_timeout` 30s；撤销 WAL 回到 DELETE 日志模式
  （WAL 在并发写下触发 `SQLITE_READONLY`，`busy_timeout` 救不了）；启动时一次性把已有
  WAL 老库切回 DELETE。（1cffb30 / 2b2035f）
- **入库前校验 `source_site` 白名单**；风控重定向识别为被阻，不再当成 0 条。（f757861 / de874ce）
- **审计 / 自检脚本假判据与硬编码路径**：审计脚本默认库路径从 `__file__` 推导；
  `repo_health` 无 upstream、merge commit 两类误报修正。（4de4701 / d31a959 / 11d612a）

### 变更

- **本地模型名收敛**：`qwen2.5:14b` / `qwen2.5-coder:7b` → `qwen3.5:4b`（客户端注释 /
  示例值 / 脚本 pull 提示）；README 历史实测表与配置文档里的旧型号作为历史留痕刻意保留。（f1950f6）
- **清除 196 条 seed 演示岗位**：`scripts/purge_seed_data.py` 默认 dry-run、只动
  `source_site='seed'`；`seed_demo_data.py` 补「真实采集阶段勿再跑」警示。（1b27fe2）
- **测试基线**：用例由 1069 项推进到 **1126**，测试文件由 72 个推进到 **75**
  （基线提交 91be3f0 / d1366a6 → 93c4753；用例数以演示机当场 pytest 汇总为准，本次发版工位未复跑全量）。

### 文档

- 演示与答辩现场手册按仓库现状重写（`docs/demo-playbook.md`）；README 测试数统一、
  端点分模块表重算；补数据来源口径与故障排查两篇文档；排查文档去掉本机绝对路径，
  showcase 路由模块数与失效爬虫路径订正。（8e7f44c / 2f0c5ef / 3dc6a8f / 96632e1 / 67c6662 / df83fcf）

---

## [1.1.0] - 2026-10-29

本轮主线是「把『验证通过』从自报变成可复现」，顺带补齐声明式数据源的最后一环。

### 新增（声明式数据源：详情页抓取）

- `app/crawlers/specs/*.yaml` 的 `detail.*` 从"只解析"变成"真执行"：列表页每条按 `detail.link_field`
  取链接 → 抓详情页 → 按 `detail.fields` 补齐字段。**详情同名字段覆盖列表值**，取不到时保留列表原值
  （不用空值洗数据）。
- 请求量三道闸：`detail.max_items`（缺省 10，显式 0 = 不抓）、每条请求前 `_interval()` 节流
  （复用既有实现，未另造 sleep）、命中风控**停止后续请求**（不是跳过这条继续打）。
  单条失败只降级该条并计入 notes，不整批作废。
- 加载期校验同步放开：`detail.fields` **仅当** `enabled` 与 `link_field` 齐全时才允许被 `field_map` 引用，
  否则照旧报错 —— 否则就是一个"配置写错、永远取空值"的坑。
- `docs/data-source-plugin.md` 补 §六 与三条约束，删掉所有"尚未实现"的表述。

### 修复（安全：同一个 SSRF 的第二处出口）

- `job_checker.check_job_url()` 此前直接 `httpx(follow_redirects=True)` 请求 `job.url`，而 `job.url`
  是**用户可写**的（链接导入 / 手动新增 / CSV 导入）—— 后台定时核查等于替人去打内网地址。
  改为复用 `url_safety.safe_get()`（**先判后发**、逐跳校验），并把 `url_import_service` 里的重复实现上移复用。
- 语义边界保持不变：网络失败、被安全规则拒绝都不判"下线"，但后者改 `warning` 留痕
  —— 降级成 debug 会和"网络抖动"长得一样，排查时直接被忽略。

### 修复（健壮性：系统抽取的文本超长会 422）

- **用户直接提交**超长 → 422（保留，用户有能力改小重提）；**系统自己抽取**的超长 → 截断 + `warning` 留痕
  （页面明明抓成功了，却让用户"确认入库"失败，说不通）。上限由 `JOB_TEXT_LIMITS` 单一真源供给两端。
- 顺带修掉一个静默缺陷：抽取结果用 `description` 键、视觉链路用 `job_desc`，此前链接导入的岗位描述
  **被静默丢弃、落库恒为空**。

### 修复（打包：装在 D 盘起不来）

- `database.py` 判 sqlite 绝对路径用的是 `startswith(("C:\\", ...))` 白名单，只认 C 盘。打包版库路径由
  exe 同级目录拼出，装在 `D:\...` 时被判成相对路径 → `sqlite+aiosqlite:///./D:\...` →
  **Windows 文件名不允许冒号，双击 exe 直接起不来**。
- 判据改为路径语义（Windows 或 POSIX 语法任一为绝对即绝对），`:memory:` 原样透传；补 11 条断言。
- 为什么长期隐身：两个 frozen 测试用 `tmp_path`，它永远在 C 盘 —— 恰好落在旧白名单里。
  **不是逻辑写错，是测试所在的位置让错误无法暴露。**

### 变更（门禁与 CI）

- 新增 `.githooks/pre-push`：拦直推 `main`/`uat`/`dev`、凭据误入库、Python 语法（**刻意不跑测试**）。
- CI 补前端 job（typecheck / XSS 消毒 / build）；触发分支收窄为镜像库真实拥有的三条，
  删掉永不命中的 `dev-merge/**`（配置里写着"有 CI"，实际一次都不会触发）。
- 验收固化成一行命令 `verify-sha.sh`：固定 SHA → 独立工位 → 全量测试 → **按通过数判定**
  （退出码会被本机沙箱的临时目录清理干扰，只认退出码会把正常验收判成失败）。

### 文档

- `docs/showcase.md` 的数字与口径按实测校正：937 项测试 / 65 个测试文件 / 104 个端点 / 22 个服务 /
  约 1.6 万行 Python；本地模型统一 `qwen3.5:4b`（14b 实测 offload 仅 12.2 tok/s）。
- 新增 `docs/demo-playbook.md`：演示前清单、三分钟点击脚本、五个可深挖技术点、翻车兜底（内部使用）。

### 安全（默认配置不再等于「管理员公开」）

四条默认值单独看都不致命，叠加起来是「双击运行 = 同一 WiFi 下任何人都能拿到管理员 token」：
打包入口写死 `host=0.0.0.0` + `app_env` 默认 `development` + 免密登录只判环境不判来源
+ 初始口令写死在源码里。

- **默认只监听回环**：`run_entry.py` 由 `0.0.0.0` 改为 `127.0.0.1`。这是个单机桌面软件，
  没有对外服务的需求；需要局域网/容器访问时显式设 `SERVER_HOST=0.0.0.0`。
- **免密登录增加来源判定**：`/api/auth/auto-login` 与 `/api/auth/default-username`
  仅在**回环地址**可用，局域网来源一律 403。判定用 `ipaddress.is_loopback` 而不是
  `startswith("127.")`（后者连 `127.0.0.1.evil` 都会放行，已被单元测试抓到）。
  前端依赖这两个接口做静默登录，所以是收紧来源而非删除接口。
- **生产密钥校验改按特征拒绝**：`change_me_to_a_long_random_secret_for_production`
  长 48 字符能穿过「至少 32 位」的长度门，但它是仓库里的公开常量 ——
  任何读过源码的人都能离线签发任意 role 的 token。改为「特征片段 + 精确值」双判据：
  只枚举具体值必然漏掉下一个新增的模板（`config.template.env` 里就还有一个变体）。
- **初始口令不再写死**：支持 `ADMIN_INITIAL_PASSWORD`；生产环境未设置时随机生成
  并写入 `data/admin-initial-password.txt`，日志不再输出明文口令。
  开发环境仍沿用 `admin/admin123`（与 README 一致，否则「照文档跑却登不进去」）。

前端 `v-html` 的两处正则黑名单换成 DOMPurify 白名单。实测旧实现对
`java&#9;script:`、`<math><mtext><script>` 这类变形必然失效 ——
根因是 HTML 不是正则语言。`frontend/scripts/xss-check.mjs` 可用
`node scripts/xss-check.mjs` 复跑 18 个 payload 与排版保留断言。

### 修复（事务与连接生命周期）

- **操作日志会把半截修改一起提交**（`app/utils/log_decorator.py`）：原先复用业务
  session 并在 `finally` 里 `commit`。接口在「已改字段、尚未提交」时抛 400（如角色
  校验失败），那把 commit 会把半截修改落库 —— 调用方收到失败，数据却变了。
  改为独立会话写日志；对照实测：旧实现提交了、新实现没有，且日志仍然写入。
- **SSE 接口用已关闭的会话**（`app/routers/ai.py`）：`StreamingResponse` 是「先返回
  响应、后迭代生成器」，而依赖注入的清理发生在响应返回之前 —— 流真正开始时，
  `Depends(get_db)` 给的 session 已经被关掉，之后再用它会重新借连接且无人 close。
  改为在生成器内部 `async with async_session()`。同一结论项目已在
  `compat.py` 的后台任务里写过，这里当时漏改。

### 测试

- 792 → 845 passed。新增 `tests/test_security_defaults.py`（25 条）与
  `tests/test_salary_and_ingest.py`（28 条）。
- 修 3 个测试夹具里 `db_session.commit()` 未 `await` —— 数据能查到只因 SQLAlchemy
  的 autoflush，实际没有提交，等于「测了个寂寞」。

### 修复（打包产物，均为「测试全绿但产物不可用」类缺陷）

- **通用版 exe 在干净机器上启动即崩**：`ModuleNotFoundError: No module named 'aiomysql'`。
  根因是打包环境残缺且无人校验 —— `app/database.py` 的方言驱动是 SQLAlchemy
  **运行时动态 import** 的（驱动名拼在 URL 字符串里，文件里没有 `import aiomysql`），
  所以 PyInstaller 静态分析看不到；`recsys.spec` 的 `hiddenimports` 虽已声明，
  但 `hiddenimports` 只能「让打进去」，不能凭空变出**没安装**的包。
  那次打包用的解释器只装了 `pyinstaller` + `aiosqlite`，于是静默打出缺驱动的产物。
  → `build-exe.bat` 新增打包**前**（依赖可导入）与打包**后**（驱动确实在 `_internal/`）
  两段校验，任一失败即 `exit /b 1` 中止，不再产出残缺 exe。
- **通用版默认连 MySQL 而非内置 SQLite**：`config.py` 的 `db_type` 默认值是 `mysql`，
  而 `config.template.env` 承诺「零依赖、不需要安装 MySQL」。
  新用户下载后**还没放 config.env** 就双击 exe（第一个动作），
  默认值会指向 `mysql+aiomysql://localhost:3306` → 目标机器没有 MySQL → 启动失败。
  开发机上跑着 MySQL，源码模式连得上，**所有测试也都是源码模式跑的**，
  所以这个区间从未被覆盖。
  → 打包版（`sys.frozen`）默认 `db_type=sqlite`；源码模式行为不变。
- **打包版数据库文件会随启动目录漂移**：SQLite 分支原用相对路径 `./recruitment_db`，
  库落到哪取决于 CWD —— 双击图标时恰好是 exe 目录（碰巧对），
  但从别处命令行启动、或快捷方式改了「起始位置」时就落到别处，
  用户会以为「数据丢了」。绿色软件承诺「整个目录拷走即用」，必须是绝对路径。
  → 打包版 `db_name` 固定为 exe 同目录下的绝对路径。

### 修复（数据库结构演进）

- **旧库缺列会让整张表的接口全部 500**：`Base.metadata.create_all` 只创建不存在的表，
  **不会给已存在的表加列**（SQLAlchemy 既定语义，不是 bug）。于是「给模型加一列」
  在旧库上**静默不生效**，而失败时机极晚：
  ① 启动日志完全正常（create_all 无报错、默认管理员创建成功）；
  ② 直到有人访问该表的接口才抛 `Unknown column` / `no such column` → HTTP 500；
  ③ **影响面是该表的全部查询，而不是「用到新列的那一个」** ——
  因为 SQLAlchemy 的 `SELECT` 会列出模型里的所有列。给 `job` 加一列，
  会让岗位列表、统计图表、AI 工具查询**同时**挂掉。
  以往靠 README「表结构变更」的手工补列清单兜底，但**该清单已经漏项**：
  `job.last_checked_at` 是后加的列，其补列语句从未被写进 README ——
  文档是给人读的，而 schema 漂移会在没人读文档时爆发。
  → 新增 `app/schema_sync.py`：启动时比对「模型定义的列」与「数据库实际的列」，
  对缺失的列执行 `ALTER TABLE ... ADD COLUMN`。**只增列**，绝不删列或改类型
  （`ADD COLUMN` 对已有数据安全：新列取 NULL 或服务端默认值）；
  主键 / 唯一约束 / `NOT NULL` 且无默认值的列**不自动处理**，改为明确报错 ——
  这类改动不可能是「无感升级」，需要人工判断。
- **自动化边界**：索引**不**跟着自动补，并在 README 写明原因 ——
  `CREATE INDEX` 在已有数据的表上是 O(n) 且可能锁表，放进启动流程会让服务卡住；
  而 `ADD COLUMN` 只是改元数据。「能自动」不等于「该自动」。

### 修复（鉴权与错误提示）

- **`/api/jobs/*` 下 18 个接口未要求登录**：岗位列表 / 详情 / 7 个统计 / 分析 /
  薪资预测 / 推荐 / 技能画像，以及兼容层的 2 个 —— **全都没有认证依赖**，
  任何人无需登录即可读取岗位数据。实测：无 token 请求 `POST /api/jobs/page`
  返回 200 + 岗位数据；`GET /api/jobs/predict-salary` 返回薪资区间数字。
  而同项目其它接口（`/api/system/version`、`/api/user/list`）都返回 401 ——
  **说明这是遗漏而非有意公开**。
  → `jobs.py` 在 **router 级**声明 `dependencies=[Depends(get_current_user)]`：
  一行覆盖全部现有接口与将来新增的接口，比逐个接口夹依赖更不易漏。
  兼容层 `compat.py` 用完整路径注册、**不受 router 级依赖覆盖**，
  那两个接口单独声明 —— 这个"覆盖不到"是**由本次新加的系统性鉴权测试发现的**，
  也正说明了那类测试的必要性。
- **兼容接口 `default-username` 生产环境未设防、明文泄露管理员用户名**：
  它无条件返回 `{"username": "admin"}`。而**紧邻它**的 `auto-login` 已经做了
  「生产环境 403」的防护，并在 docstring 写明"生产环境绝不能出现后门" ——
  同一段代码里两个同类接口的安全策略不一致，**这本身就是它被漏掉的信号**。
  已按既有模式补上：生产环境 403。
- **前端读错响应字段名，导致所有错误提示失效**：前端读 `res.msg` / `data.msg`，
  而后端契约字段是 **`message`**（`Result` schema 与三个异常处理器都只用
  `message`，全项目不存在 `msg` 字段）。实测：

  ```
  HTTP 400  {"code": 400, "message": "配置值不能包含换行：deepseek_api_key", ...}
  前端读 data.msg     → None                        ← 拿不到
  前端读 data.message → '配置值不能包含换行：...'    ← 提示在这里
  ```

  后果：后端所有可读提示被吞掉 —— 业务错误显示固定的「请求失败」，
  走异常分支的则回退到 axios 的 `error.message`，用户看到英文报错。
  **它让此前所有"让错误可诊断"的工作（500 改为业务错误、配置写入的友好提示、
  采集任务失败原因）在最后一公里失效。** 已改为优先读 `message`、`msg` 兜底。

### 新增

- **系统性鉴权覆盖测试** `test_岗位接口全部要求登录`：遍历 `app.routes` 枚举
  `/api/jobs` 下全部路径，断言无 token 访问一律不放行。它不针对某个接口，
  而是针对**鉴权覆盖本身** —— 一次性发现了 2 个漏网接口（在 `compat.py` 里），
  也确保 `jobs.py` 那行 router 级依赖不会被误删（已实测：注释掉该行后本用例立即失败）。
- **生产环境禁用兼容接口的回归测试**：`default-username` 在生产环境返回 403。
- **打包完整性回归测试** `tests/test_packaging_integrity.py`（6 项）：
  覆盖「依赖清单 / spec hiddenimports / 构建脚本自检 / 驱动动态导入」四个环节，
  以及「打包版默认零依赖数据库 / 库文件落在 exe 同目录」。
  每项都做过「撤掉修复必须失败」的验证，不是永远绿的假回归。
  这类缺陷的共同特征是：**测试覆盖了代码逻辑，但没覆盖打包产物在陌生环境里的行为** ——
  所以必须专门测「frozen 模式下的默认值」与「产物里确实有驱动」。
- **数据库结构自检回归测试** `tests/test_schema_sync.py`（12 项）：
  覆盖「缺列自动补齐 / 补齐后查询恢复 / 幂等 / 只增不删 / 空库交给 create_all」，
  以及安全边界（主键、唯一、NOT NULL 无默认值都不自动处理）。
  其中一条走**真实 lifespan** 端到端验证「旧库启动后列真的被补上」——
  因为函数级测试**测不到「接线」**：lifespan 里那句调用被删掉时，
  函数级用例仍会全绿，而缺陷原样回归（已实测确认这条端到端用例能抓到）。

---

## [1.0.0] - 2026-09-17

首个可交付版本：可面试的完整项目 + 开箱即用的绿色版 + 可迭代的发布机制。

### 新增

- **前程无忧（51job）采集平台**：走该站前端自用的 JSON 搜索接口取数，
  字段别名容错、城市编码（`jobArea`）与薪资（`1-1.5万` / `8千-1.2万` / `15-25万/年` / `·13薪`）
  独立解析并统一折算月薪
- **平台注册表** `app/crawlers/registry.py`：平台标识 → 爬虫类收敛到一处，
  支持集合由注册表派生，新增平台只改一行
- **日报 Excel 数据透视**：新增「汇总统计（城市 × 平台 交叉表，带合计行列）」与
  「平台分布」两个 sheet，共 8 个 sheet
- **日报 Webhook 推送**：企业微信 / 钉钉 / 通用 JSON，默认关闭；
  按业务错误码（`errcode != 0`）判定失败，而非只看 HTTP 状态码
- **定时任务可配置**：爬取与日报的触发时间、开关从代码移入配置
- **版本自证与更新检查**：`GET /api/system/version`（版本 + 发行形态 + 差异化升级指引）、
  `GET /api/system/update-check`（比对 GitHub Release）；版本号收敛到 `app/version.py` 单一来源
- 新增「采集节流」相关的平台级请求前置校验（未收录城市 / 未实现平台直接拒绝，不发请求）

### 修复

- **【P0】`BossCrawler.crawl()` 缺失 `return`**：抓到的岗位全部被静默丢弃而任务仍记为
  COMPLETED。此前无任何用例执行过 `crawl()` 本体（测试把 `crawl_with_retry` 整个换成桩函数）
- **【P0】通用版 exe 在干净机器上无法启动**：建表只存在于 `scripts/init_db.py`，
  打包产物无任何建表入口且不带预置数据库 → 空库启动即 `no such table: user`。
  现由 `lifespan` 幂等 `create_all` 兜住
- **SQLite 下导入期崩溃**：`create_async_engine` 无条件传 `pool_size` / `max_overflow`，
  而 SQLite 内存库走 StaticPool、不支持这两个参数 → `TypeError`
- **测试不 hermetic**：`conftest` 未清 `deepseek_api_key`，本机 `.env` 配了真实 key 的
  开发者会走到 primary 分支，令降级类用例失败、结果随本机配置漂移

### 变更

- 路由与接口文档版本号改为引用 `app/version.py`，不再写字面量
- 新增 `tests/test_startup_bootstrap.py`：用子进程跑真实 lifespan 覆盖「空库启动」，
  该前提此前无任何用例触及

---

## [0.3.0] - 2026-09-13

### 新增

- **ReAct 多步推理 Agent**（`app/services/react_agent.py` + `POST /api/ai/agent-stream`）：
  `Thought → Action → Observation` 循环，多回合调用工具解决复合问题；
  带步数上限与无 `Final Answer` 时的强制收尾，绝不无限循环

---

## [0.2.0] - 2026-09-10

### 新增

- **RAG 引用溯源**：回答透出命中的知识条目（`event: sources`）与模型元信息
  （`event: meta`：provider / 降级层级 / token / 耗时）
- **平台与城市可选项接口**：`GET /api/crawler/options` 结构化暴露平台中文名与是否已实现，
  前端不再硬编码
- 浏览器端实测验证（前端渲染 17 项）

### 修复

- 登录响应平铺为 `UserVO` 契约（嵌套结构会让前端 `role` 丢失，
  导致 4 个 `/admin/*` 页面被静默重定向回首页）
- 知识库检索改为在降级链之外只做一次（`usage_count` 曾虚增 3 倍）
- 平台归一化收敛到服务层入口（只挂在兼容层时，同一输入走不同入口结果不同）

---

## [0.1.0] - 2026-09-07

### 新增

- FastAPI 后端骨架：爬虫（BOSS 直聘）、智能推荐（Jaccard + 薪资预测）、
  知识库、AI 对话（SSE 流式 + 三级降级链）
- 知识库升级为语义向量检索 + Function Calling + 技能归一化
- 项目 README（技术栈 / 模块说明 / 快速开始）

---

## 更正：上一版误标为「计划中（未排期）」的两项（2026-10-29）

> 上一版把它们列进「计划中（未排期）」，并说明只是方向 —— **事实上两项都已在
> [1.0.0]（2026-09-17）随发布落地**。本节改为指认实现位置，避免后来人
> （包括面试官）读到"文档说没做"就认为能力缺失。

- **浏览器扩展 / 油猴脚本采集通道**：已于 **1.0.0** 实现，见
  `userscript/recsys-collector.user.js`（真实登录态的浏览器里按人操作节奏采集）；
  回传入口 `POST /api/crawler/ingest`（令牌头 `X-Collect-Token`），
  用例 `tests/test_browser_ingest.py`（23 项，含平台标识一致性与"采集不得触发下架判定"）
- **robots.txt 实际接入**：已于 **1.0.0** 实现，见 `app/crawlers/robots.py`
  （按域名缓存 1 小时、`crawl_robots_strict` 严格 / 宽松两种处置）；
  调用点是 `app/services/crawler_service.py` 的 `robots.gate(platform, probe)`，
  用例 `tests/test_throttle.py`
