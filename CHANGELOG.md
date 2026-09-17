# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。
**版本号的唯一来源是 `app/version.py` 里的 `APP_VERSION`**，
GitHub Release 的 tag 必须与之一致（`v1.0.0`）—— 更新检查就是靠比对两者判断有没有新版。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> **关于 0.x 条目**：这几条是按 git 提交历史**回溯整理**的里程碑（当时未打 tag），
> 用于说明项目是怎么长起来的；从 `1.0.0` 起，版本号、tag、Release 三者一一对应。

---

## [Unreleased]

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

### 新增

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

### 计划中

- 浏览器扩展 / 油猴脚本采集通道：在真实登录态的浏览器里按人操作节奏采集，
  从原理上规避无头浏览器指纹与 IP 集中访问（详见 README「采集通道」一节）
- robots.txt 实际接入：`BaseCrawler.check_robots_allowed()` 已实现解析逻辑，
  但**从未被任何调用方使用**，目前等于没有合规检查

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
