# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。
**版本号的唯一来源是 `app/version.py` 里的 `APP_VERSION`**，
GitHub Release 的 tag 必须与之一致（`v1.0.0`）—— 更新检查就是靠比对两者判断有没有新版。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> **关于 0.x 条目**：这几条是按 git 提交历史**回溯整理**的里程碑（当时未打 tag），
> 用于说明项目是怎么长起来的；从 `1.0.0` 起，版本号、tag、Release 三者一一对应。

---

## [Unreleased]

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
