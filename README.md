# AI 招聘数据可视化系统（Python 重构版）

> 毕业设计《AI 招聘数据可视化系统的开发与设计》的 Python/FastAPI 重构实现。
> Java 原版见：`github.com/zanboring/Recruitment`

## 一、项目简介

面向招聘数据分析场景的全栈系统，覆盖 **数据采集 → 清洗入库 → 统计分析 → 可视化展示 → AI 智能服务** 完整链路。

本仓库是毕设的 **Python 重构版**：用 FastAPI + SQLAlchemy 2.0 异步 ORM 重写后端，保留全部业务能力，并强化了 AI 服务、知识库与推荐模块。

**核心数据**：9 个业务模块 / 约 58 个 REST 接口 / 系统完成度 92%

## 二、技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI 0.115 + Uvicorn |
| ORM / 数据库 | SQLAlchemy 2.0（asyncio）+ aiomysql + MySQL |
| 数据校验 | Pydantic 2.9 + pydantic-settings |
| 认证 | JWT（python-jose）+ passlib/bcrypt |
| 定时任务 | APScheduler 3.10 |
| 爬虫 | httpx + BeautifulSoup4 + lxml + Playwright |
| AI 接入 | httpx 流式调用（云端 GLM-4-Flash / 本地 Ollama） |

## 三、模块说明

```
app/
├── main.py            # FastAPI 应用入口
├── config.py          # 配置（环境变量）
├── database.py        # 异步数据库连接与会话
├── scheduler.py       # 定时爬取任务
├── routers/           # 9 个路由模块：ai / auth / crawler / jobs / knowledge / log / model / user
├── services/          # 9 个业务服务层
├── models/            # 7 个 SQLAlchemy 模型
├── schemas/           # 7 组 Pydantic Schema
├── crawlers/          # 爬虫：BaseCrawler 抽象类 + BossCrawler + 清洗器
├── recommender/       # 推荐算法：Jaccard 相似度 + 薪资预测 + 多因子分析
└── utils/             # 工具函数
```

### 1. AI 服务模块（9 个接口）

- **三级降级链**：云端 GLM-4-Flash → 本地 Ollama（qwen2:7b）→ 规则引擎兜底，任一后端不可用时自动降级，保证服务不中断
- **SSE 流式输出**：基于 httpx 流式响应 + `EventSourceResponse`，前端打字机效果
- **会话管理**：支持会话取消、最大会话数 1000、单会话保留最近 20 条历史
- **模型管理**：6 个接口，支持模型配置的增删改查与启用切换

### 2. 知识库模块（12 个接口）

- **关键词检索**：46 个关键词自动打标签，精确匹配取 Top-3、模糊匹配取 Top-5
- **缓存**：检索结果缓存 600 秒，降低数据库压力
- **质量门槛**：`learn_from_response` 对回答做质量校验，长度 < 20 字不入库，避免脏数据回流

### 3. 数据采集与清洗

- **抽象基类 `BaseCrawler`**：统一调度、限速、重试、去重流程，`BossCrawler` 继承实现平台解析
- **反爬策略**：10 个 User-Agent 轮换、请求间隔 12–18 秒随机延时、失败后 4 次指数退避重试
- **清洗规则**：9 个高级词 / 10 个无效词过滤规则，68 项技能词典抽取
- **去重**：SHA-256 对岗位指纹去重，避免重复入库

### 4. 智能推荐

- **Jaccard 相似度**：岗位技能集合与候选人技能集合求交并比
- **规则薪资预测**：9 档城市 × 6 档经验 × 4 档学历 交叉基准 + 技能溢价 15%
- **多因子加权**：相似度 0.7 + 薪资匹配 0.2 + 其它 0.1，候选池上限 500

### 5. 认证与权限

- JWT Token，有效期 24 小时
- 角色区分 ADMIN / USER，登录失败次数锁定

### 6. 数据可视化

- 后端提供 7 个维度的统计聚合接口
- 前端（Vue3 + ECharts）：饼图 3 个 / 柱状图 10+ 个 / 折线图 1 个 / 雷达图 1 个

## 四、快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（见 ENV_CONFIG.md）
#    需设置 MySQL 连接、JWT 密钥、可选 GLM API Key

# 3. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. 访问接口文档
#    http://localhost:8000/docs
```

## 五、与 Java 版的对比

| 维度 | Java 版 | 本版（Python） |
|---|---|---|
| 框架 | Spring Boot 3.2.5 + Java 21 | FastAPI 0.115 + Python 3 |
| ORM | MyBatis / JPA | SQLAlchemy 2.0 异步 |
| 爬虫 | WebMagic + ParserFactory 工厂模式（4 平台） | BaseCrawler 抽象 + BossCrawler |
| AI 接入 | 同步调用 | httpx 流式 + SSE + 三级降级 |
| 并发 | 线程池 | asyncio 原生异步 |

详细的双栈对比见 `java_vs_python_comparison.md`，迁移说明见 `MIGRATION_GUIDE.md`。

## 六、说明

- 根目录的 `audit_report_v*.md` 为开发过程的代码审计报告（迭代版本），`audit_report_v8.md` 为最终版
- 数据库建表与初始化数据见 `app/init_data.py`
