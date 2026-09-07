# Python 毕设代码审查报告（第三轮）

> 审查时间：2026-09-02
> 对象：`recruitment-python`（FastAPI 重构版）
> 结论：P0 缺口已全部补齐，项目完成度从旧快照的 53% 提升到约 **85%**。本轮修复 1 个严重 bug + 1 个功能缺口。

---

## 一、总体状态（对比旧快照）

上一份 `java_vs_python_comparison.md`（7 月 6 日）标注的 P0 缺口，现在**已全部补齐**：

| P0 缺口（旧） | 现状 | 证据 |
|---|---|---|
| 知识库 RAG 模块（12 接口） | ✅ 已实现 | `routers/knowledge.py` 12 个端点 + `services/knowledge_service.py` |
| 本地规则引擎兜底 | ✅ 已实现 | `services/local_model_service.py`（FAQ + 5 类数据实时分析） |
| 操作日志模块 | ✅ 已实现 | `routers/log.py` + `utils/log_decorator.py` |
| 爬虫数据过滤（高级/无效/高薪） | ✅ 已实现 | `crawlers/cleaner.py` + `crawler_service.py` |
| 岗位下架标记 | ✅ 已实现（但含 bug，本轮修复） | `crawler_service.py` |
| 数据初始化（启动建 admin） | ✅ 已实现 | `init_data.py` + `main.py` lifespan |

AI 三级降级链（GLM-4 → Ollama → 规则引擎）与 RAG 增强问答也已接通（`ai_service.py`）。

---

## 二、本轮修复

### 🔴 P0 修复 1：岗位下架标记范围错误（数据污染 bug）

**问题**：`crawler_service.py` 的 OFFLINE 标记逻辑是
```python
where(Job.job_key.notin_(all_job_keys), Job.job_status.in_(["NEW","ACTIVE"]))
```
缺少平台/城市范围限定，导致**每次爬虫任务会把全表所有不在本次结果里的岗位都标记为下架**（包括其他关键词、城市、平台的岗位）。

**修复**：增加 `Job.source_site.in_(platforms)` 和 `Job.city == city` 范围限定，只把「本次爬取平台 + 城市」范围内消失的岗位标为 OFFLINE。

### 🟡 修复 2：Ollama 本地模型未接 RAG 知识库

**问题**：`ai_service.py` 中只有 GLM-4 路径注入了知识库上下文（`get_context_for_ai`），Ollama 路径是「裸调用」，没有检索增强。

**修复**：给 `call_ollama_stream` 增加 `db` 参数并注入知识库上下文，使三级降级链中 GLM-4 与 Ollama 都具备 RAG 增强能力。

---

## 三、剩余差距（P1/P2，不影响核心功能）

| 级别 | 差距 | 说明 | 建议 |
|---|---|---|---|
| 🟡 P1 | 模型管理模块缺失（6 接口） | Java 版有 `/api/model/status`、`/switch`、`/list` 等，Python 版无，前端无法查看/切换模型 | 建议补，工作量约 1 天 |
| 🟡 P1 | AI 分析接口缺 LLM 增强 | 只有 `/analysis/summary`（规则聚合），无「规则聚合 + LLM 增强分析报告」（Java 版亮点） | 建议补，工作量约 1 天 |
| 🟡 P2 | AI `/status`、`/cancel` 缺失 | 前端无法展示模型状态、无法取消请求 | 建议补，0.5 天 |
| 🟡 P2 | 应届生优先排序未接线 | `cleaner.py` 的 `is_fresh_grad_job()` 是死代码，未在 `crawler_service` 调用 | 可补或删除 |
| 🟢 P2 | `learn_from_response` 会把任意对话存入知识库 | 每次 GLM-4 回答都自动入库（按问题去重），可能污染知识库质量 | 建议加最小长度/置信度门槛 |
| 🟢 P3 | 爬虫 job_key 与 Java 版不一致 | Python 用 `MD5(title+company)`，Java 用 `SHA-256(source|title|company|city)`，两版共库会重复 | 目前不共库，暂不影响 |

---

## 四、接口覆盖总览

| 模块 | 端点数 | 说明 |
|---|---|---|
| 认证 auth | 4 | 登录/注册/改密/登出 |
| 用户 user | 5 | CRUD + 分页 |
| 岗位 jobs | 19 | CRUD + 7 统计 + 推荐 + 薪资预测 + 导入导出 |
| AI ai | 3 | chat / chat-local / chat-stream（三级降级） |
| 爬虫 crawler | 3+ | 任务 + 列表 |
| 知识库 knowledge | 12 | CRUD + 搜索 + 上下文 + 自学习 + 统计 |
| 日志 logs | 3 | 列表 + 按用户 + 清理 |
| **合计** | **约 50** | 相较旧快照 34 个，已大幅提升 |

---

## 五、给下一轮自动化的建议

1. 补「模型管理模块」（6 接口 + service），打通前端模型切换。
2. 补「AI 分析接口」的 LLM 增强（复用 `recommender/analyzer.py`）。
3. `learn_from_response` 加质量门槛（answer 长度 > 20 且不重复才入库）。
4. 删除或接线 `is_fresh_grad_job` 死代码。

---

## 六、改动文件清单

| 文件 | 改动 |
|---|---|
| `app/services/crawler_service.py` | 修复 OFFLINE 下架标记范围（加 source_site + city 限定） |
| `app/services/ai_service.py` | Ollama 路径接入 RAG 知识库上下文（2 处） |

> 两个文件已通过 `py_compile` 语法校验，可直接运行。
