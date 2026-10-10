# 项目学习路线（从地基到吃透）

> 学习者：张博仁　｜　开始日期：2026-09-23
> 项目：recruitment-python · 招聘数据采集与 AI 分析系统
> 目标：**吃透这个项目**，不追面试题，按地基 → 结构 → 特色模块的顺序推进。

---

## 学习原则（三条，先记住）

1. **先地图后细节**：每进入一个新阶段，先看它在全局的什么位置，再钻进去看代码。
2. **每课必须能"讲出来"**：每学完一块，用大白话给我复述一遍。讲不清 = 没学会，不往下走。
3. **不改代码也能学**：学习期不动项目代码。想验证就写独立的小脚本（放在 `_learning/` 下），跑完就删。

---

## 全局地图（已讲）

```
① 数据来源 → ② 采集与清洗 → ③ 数据存储 → ④ 业务与 AI → ⑤ 结果输出
```

后端一次请求的穿越路径：

```
请求 → 中间件 → 路由 → 依赖注入 → 业务服务 → 数据访问 → 数据库
                 （响应再原路返回）
```

---

## 九阶段路线

| 阶段 | 主题 | 难度 | 状态 |
|---|---|---|---|
| 0 | 全局地图与目录导航 | ★ | ✅ 已讲 |
| 1 | Web 与 HTTP 基础 | ★★ | ⬜ |
| 2 | Python 异步与 FastAPI 核心 | ★★★ | ⬜ |
| 3 | 数据库与 ORM | ★★★ | ⬜ |
| 4 | 爬虫子系统（项目特色） | ★★★★ | ⬜ |
| 5 | AI 接入层（项目特色） | ★★★★ | ⬜ |
| 6 | 推荐与评估算法 | ★★★ | ⬜ |
| 7 | 前端 Vue3 | ★★★ | ⬜ |
| 8 | 测试 · 部署 · 工程化 | ★★★ | ⬜ |

---

### 阶段 0 · 全局地图与目录导航 ✅

- **目标**：说清这个系统解决什么问题、由哪几块组成、数据怎么流动。
- **看懂的代码**：`README.md` 技术栈表、`app/main.py`（只需看懂 `create_app()` 的组装顺序）
- **自测**：合上文档，画出五段式数据流图，并指出每一段对应哪个目录。

### 阶段 1 · Web 与 HTTP 基础

- **目标**：搞懂「为什么需要后端」「浏览器怎么和后端说话」。
- **要掌握**：HTTP 请求/响应结构、GET/POST/PUT/DELETE、状态码（200/401/403/404/422/429/500）、Header、JSON；前后端分离的意义；RESTful 设计。
- **动手**：用 `curl` 亲手打这个项目的 3 个接口（登录 → 查岗位 → 看版本），观察请求和响应长什么样。
- **看懂的代码**：`app/routers/system.py`（最简单的 router）
- **自测**：能解释「前端点一下按钮，到数据库取出数据再显示出来」完整链路。

### 阶段 2 · Python 异步与 FastAPI 核心

- **目标**：理解 FastAPI 的三件套：路由、Pydantic 校验、依赖注入。
- **要掌握**：`async/await` 到底解决什么问题（IO 密集 vs CPU 密集）；ASGI 与 WSGI 的区别；中间件的"洋葱模型"；`lifespan` 生命周期。
- **看懂的代码**：`app/main.py` 全文（重点：中间件添加顺序的注释）、`app/middleware/rate_limit.py` 逐行、`app/dependencies.py`、`app/schemas/*.py`
- **自测**：解释 `app/main.py` 里「限流先加、CORS 后加」为什么这么排；解释 `lifespan` 里为什么要先建表再启动调度器。

### 阶段 3 · 数据库与 ORM

- **目标**：搞懂数据是怎么存进去、怎么取出来的。
- **要掌握**：表/行/列/主键/外键/索引；SQL 与 ORM 的对应关系；SQLAlchemy 2.0 异步写法的固定套路；连接池；事务。
- **看懂的代码**：`app/models/` 全部 8 张表、`app/database.py`、`app/schema_sync.py`
- **产出**：画出 8 张表的 ER 关系图（用 mermaid），标注每张表干什么用。
- **自测**：解释 `create_all` 为什么"不给已存在的表加列"，以及项目用 `schema_sync` 怎么绕开的。

### 阶段 4 · 爬虫子系统（项目特色 ①）

- **目标**：理解爬虫本质，以及"能跑"到"跑得稳"之间的距离。
- **要掌握**：HTTP 抓取 + HTML 解析的基本套路；静态页 vs 动态渲染页，为什么需要 Playwright；限速与指数退避重试；robots.txt 合规；去重策略；数据清洗。
- **看懂的代码**：`app/crawlers/base.py` → `boss.py` / `job51.py` → `throttle.py` / `robots.py` / `cleaner.py` / `city_map.py` / `registry.py`
- **自测**：解释 `throttle.py` 的退避算法；解释任务状态从 `COMPLETED` 到 `FINISHED` 的流转；说清遇到反爬会怎么处理。

### 阶段 5 · AI 接入层（项目特色 ②）

- **目标**：理解 LLM 应用开发的真实形态。
- **要掌握**：LLM API 调用原理（OpenAI 兼容协议）；为什么需要"三级降级链"；Embedding 与向量检索（RAG 的基本原理）；ReAct Agent 与工具调用的区别；SSE 流式输出。
- **看懂的代码**：`app/services/llm_client.py` → `model_service.py` → `ollama_client.py`；`embedding_service.py`、`knowledge_service.py`、`react_agent.py`、`tool_service.py`、`app/routers/ai.py`
- **自测**：解释"云端 → 本地 Ollama → 规则引擎"的降级触发条件；解释 RAG 检索出来的内容是怎么拼进 prompt 的。

### 阶段 6 · 推荐与评估算法

- **目标**：理解推荐打分和"怎么证明算法有效"。
- **要掌握**：集合相似度（Jaccard）从数学定义到代码；画像分析思路；评估指标 Hit@K / MRR / Precision / Recall 的含义与算法。
- **看懂的代码**：`app/recommender/jaccard.py`、`analyzer.py`、`salary_predictor.py`；`app/evaluation/metrics.py`、`golden_set.py`、`runner.py`
- **自测**：手算一个 3 元素的 Jaccard 相似度；解释为什么"关键词检索的语义型 Hit@5 是 0%"。

### 阶段 7 · 前端 Vue3

- **目标**：看懂前端怎么消费后端接口。
- **要掌握**：Vue3 响应式与组件化；`ref`/`reactive`；路由（Vue Router）；状态管理（Pinia）；Axios 请求封装；ECharts 图表。
- **看懂的代码**：`frontend/src/main.ts`、`api/`、`router/`、`store/`、`layouts/`、`views/` 挑 2 个典型页面精读
- **自测**：跟踪"打开岗位列表页"从组件挂载到数据渲染的完整过程。

### 阶段 8 · 测试 · 部署 · 工程化

- **目标**：理解工程纪律。
- **要掌握**：pytest 与 fixture；为什么测试要用 SQLite 内存库；Docker 多阶段构建与 Nginx 反代；PyInstaller 打包原理；配置分层管理（`config.py` + `.env`）；SemVer 版本管理。
- **看懂的代码**：`tests/helpers.py` + 挑 3 个测试文件、`docker-compose.yml`、`docker/backend/Dockerfile`、`recsys.spec`、`app/config.py`
- **自测**：解释为什么集成测试不连真 MySQL；解释 exe 版本为什么要"资源定位三选一"。

---

## 进度记录

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-09-23 | 阶段 0：全局地图与目录导航 | 建立数据流与分层两张图 |
