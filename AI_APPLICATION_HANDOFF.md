# AI 应用开发版交接文档

## 1. 项目定位

本仓库不是 Java 项目的逐接口翻译版，而是用于投递 **AI 应用开发 / LLM 应用工程师** 岗位的独立后端作品。核心故事是：以真实招聘数据为数据源，实现了带 RAG、工具调用、流式输出、多模型降级、权限隔离和测试的 AI 招聘助手。

面试时应优先演示：

1. `POST /api/ai/chat-stream`（兼容入口：`/api/ai/stream`）的 SSE 输出；
2. 用户询问岗位数量或筛选条件时，模型识别并调用 `query_jobs` 工具；
3. 知识库的向量检索、关键词兜底和回答上下文注入；
4. GLM -> Ollama -> 规则引擎的降级链；
5. JWT、会话按用户隔离、限流和测试。

不要宣称它与 Java 版“完全等价”。Python 版要以 AI 工程能力取胜。

## 2. 当前架构

```text
FastAPI
  ├─ routers/          HTTP API、鉴权边界、SSE
  ├─ services/
  │   ├─ ai_service.py         RAG 注入、工具调用编排、模型降级
  │   ├─ knowledge_service.py  向量检索 + 关键词兜底
  │   ├─ embedding_service.py  embedding-3、缓存、余弦相似度
  │   └─ tool_service.py       query_jobs 工具定义与执行
  ├─ crawlers/         BOSS 数据采集、清洗、去重、下架标记
  ├─ recommender/      匹配、薪资预测
  └─ middleware/       滑动窗口限流
```

数据流：用户问题 -> 鉴权/限流 ->（可选）工具识别并查询岗位 -> RAG 检索 -> 云端模型 -> Ollama -> 规则引擎 -> SSE。

## 3. 重要安全约束

- `APP_ENV=production` 或 `prod` 时，启动会拒绝默认 JWT 密钥和少于 32 字符的 JWT 密钥。
- 系统环境变量优先于 `.env`；部署平台注入的密钥不会再被仓库内 `.env` 覆盖。
- `/api/model/switch`、`/api/model/reload` 必须是管理员；`/api/model/list` 和 `/api/model/chat` 必须登录；`status`、`health` 是公开健康信息。
- AI 会话键是 `user_id + session_id`，不能改回只用 `session_id`。
- 当前限流是单进程内存实现。多副本部署前必须替换为 Redis 计数器；反向代理部署时只信任已配置代理写入的 `X-Forwarded-For`。

## 4. 前端兼容策略

Java 前端不是 Python 版的唯一真相。为了不破坏既有演示，已保留 `/api/ai/stream` 作为 `/api/ai/chat-stream` 的兼容别名。

其余 Java 前端接口仍有差异（爬虫任务、数据导入导出、部分分析接口）。后续只能二选一：

- 写 `frontend/src/api` 适配层，明确使用 Python API；或
- 在 Python 端增加薄兼容路由，并用契约测试锁定响应字段。

不要在业务服务中为兼容旧接口复制逻辑。

## 5. 本地运行

```powershell
# 需要 Python 3.11+；旧 .venv 若指向 WindowsApps 路径，必须删除后重建
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts/init_db.py
python -m pytest tests -q
python -m app.main
```

`.env` 中可使用 SQLite 做演示（`DB_URL=sqlite+aiosqlite:///./recruitment.db`），生产使用 MySQL，并设置强随机 `JWT_SECRET`。不要提交 `.env`、真实 API Key 或真实数据库。

## 6. 下一位 AI/开发者的优先任务

### P0：可投递与可复现

1. 重建失效的 `.venv`，在干净环境跑全量 pytest，并记录实际通过数量。
2. 加 `docker-compose.yml`（API + MySQL，可选 Ollama），并验证一键启动。
3. 建立 OpenAPI/HTTP 契约测试，覆盖前端真正使用的 API。
4. 将默认管理员改为首次启动随机密码或强制改密，避免 `admin/admin123` 出现在演示环境外。

### P1：AI 应用面试加分项

1. RAG 返回 `sources`（知识库条目 ID、问题、相似度），前端展示引用而不是仅把上下文藏在 prompt 中。
2. 增加 20--30 条固定评测集，记录检索 Recall@K、工具调用准确率、回答可用率和降级成功率。
3. 为工具调用改用模型原生 function calling（若所选模型支持），保留当前 JSON prompt 方案作兼容兜底。
4. 加请求 ID、模型耗时、检索耗时、命中数和错误类型日志；严禁记录 token、密码、完整用户输入或 API Key。

### P2：产品完善

1. 把 RAG 文档从单条 Q&A 扩展为文档上传、切分和批处理向量化。
2. 支持 pgvector / Milvus 等向量数据库；当前内存余弦计算适合毕业作品与小数据量。
3. 给 React/Vue 前端增加来源引用、模型状态、取消输出和降级状态。

## 7. 面试讲解提纲

“我没有把 LLM 当作普通聊天接口。系统先用工具调用从招聘数据库获得可验证事实，再用 RAG 补充领域知识；生成侧采用 SSE 减少等待感，并且在云模型、Ollama 和规则引擎之间降级。为了避免常见线上问题，我处理了多用户会话串扰、AI 接口限流、模型管理权限和生产密钥校验。下一步是加入可量化的 RAG 评测和容器化交付。”

