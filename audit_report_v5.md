# Recruitment Python 项目审计报告 V5

**审计日期**: 2026-07-07  
**项目规模**: 49 文件 / 3060 行 Python + 1 HTML  
**语法检查**: ✅ 全部通过

---

## 总体评价

自上次审计以来，项目新增了 **7 个文件**（init_data、knowledge 路由/服务/schema、log 路由、local_model_service、log_decorator），补齐了之前缺失的知识库 RAG、操作日志、规则引擎兜底、初始化管理员四大模块。功能完成度从 ~90% 提升到 **~95%**。

但新增代码引入了 **8 个阻塞级问题**，主要集中在 knowledge 和 log 两个新模块。

---

## 🔴 阻塞级问题（8 个）

### 1. knowledge 路由 — `from_orm()` Pydantic v1 废弃方法（8 处）

**文件**: `app/routers/knowledge.py` 行 20, 29, 40, 50, 62, 88, 102, 133  
**问题**: 使用了 Pydantic v1 的 `.from_orm()` 方法，Pydantic v2 已废弃，未来版本将移除

```python
# 当前（废弃）
KnowledgeBaseResponse.from_orm(item)

# 应改为
KnowledgeBaseResponse.model_validate(item)
```

**影响**: 当前能跑但会产生 DeprecationWarning，Pydantic 升级后直接崩溃

---

### 2. knowledge 路由 — 响应格式与全局不一致

**文件**: `app/routers/knowledge.py` 全部接口  
**问题**: 返回 `{"code": 200, "data": ...}`，而项目其他所有接口使用 `Result` 类返回 `{"code": 0, "message": "success", "data": ...}`

```python
# 当前（错误）
return {"code": 200, "data": KnowledgeBaseResponse.from_orm(item)}

# 应改为
from app.schemas.common import Result
return Result.success(KnowledgeBaseResponse.model_validate(item))
```

**影响**: 前端统一检查 `result.code === 0`，knowledge 接口返回 `code: 200` 会被前端判定为失败

---

### 3. knowledge 路由 — 使用 `HTTPException` 而非 `AppException`

**文件**: `app/routers/knowledge.py` 行 1, 39, 64, 77, 90, 104  
**问题**: 导入并使用了 FastAPI 的 `HTTPException`，而项目其他路由统一使用 `AppException` + 自定义异常处理器

```python
# 当前
from fastapi import HTTPException
raise HTTPException(status_code=404, detail="知识条目不存在")

# 应改为
from app.exceptions import AppException
raise AppException("知识条目不存在", 404)
```

**影响**: 异常响应格式不一致，`HTTPException` 返回 `{"detail": "..."}`，`AppException` 返回 `{"code": 404, "message": "...", "data": null}`

---

### 4. log 路由 — SysLog ORM 对象直接返回导致序列化失败

**文件**: `app/routers/log.py` 行 32, 39  
**问题**: 将 SQLAlchemy ORM 对象直接放入 `Result.success()` 返回，ORM 对象无法 JSON 序列化

```python
# 当前（错误）
return Result.success({"list": logs, "total": total, ...})  # logs 是 ORM 对象列表
return Result.success(result.scalars().all())  # ORM 对象列表

# 应创建 SysLogResponse schema
class SysLogResponse(BaseModel):
    id: int
    username: Optional[str]
    action: Optional[str]
    method: Optional[str]
    uri: Optional[str]
    ip: Optional[str]
    success: int
    error_msg: Optional[str]
    created_at: Optional[datetime]
    model_config = {"from_attributes": True}

# 然后转换
return Result.success([SysLogResponse.model_validate(log) for log in logs])
```

**影响**: 调用日志列表接口会返回 500 Internal Server Error

---

### 5. log 路由 — SQL LIKE 注入风险

**文件**: `app/routers/log.py` 行 25  
**问题**: 用户输入直接拼入 LIKE 通配符，无转义

```python
# 当前（有风险）
stmt = stmt.where(SysLog.username.like(f"%{username}%"))

# 应转义（job_service.py 已有 _escape_like 函数）
from app.services.job_service import _escape_like
stmt = stmt.where(SysLog.username.like(f"%{_escape_like(username)}%"))
```

**影响**: 用户搜索 `%` 或 `_` 会导致意外匹配

---

### 6. 前端 — 薪资预测中英文值不匹配

**文件**: `app/static/index.html` 行 275-298 vs `app/recommender/salary_predictor.py`  
**问题**: 前端下拉框发送英文值（`Beijing`, `Bachelor`, `0-3years`），后端 `salary_predictor.py` 的系数表用的是中文（`北京`, `本科`, `1-3年`）

```javascript
// 前端发送（错误）
<option value="Beijing">北京</option>
<option value="Bachelor">本科</option>
<option value="0-3years">0-3年</option>

// 后端期望
CITY_COEFFICIENTS = {"北京": 1.80, "上海": 1.75, ...}
EDUCATION_COEFFICIENTS = {"博士": 1.70, "硕士": 1.35, "本科": 1.00, "大专": 0.80}
EXPERIENCE_COEFFICIENTS = {"10年以上": 1.80, "5-10年": 1.50, "3-5年": 1.20, "1-3年": 0.90, ...}
```

**影响**: 薪资预测功能完全失效，所有系数回退到默认值 1.0

**修复**: 前端 option value 改为中文
```html
<option value="北京">北京</option>
<option value="本科">本科</option>
<option value="1-3年">0-3年</option>
```

---

### 7. 前端 — loadJobs 发送错误字段名

**文件**: `app/static/index.html` 行 451, 474  
**问题**: 前端发送 `{ page: 1, page_size: 5 }`，但后端 `JobQueryDTO` 期望 `page_num` 不是 `page`

```javascript
// 当前（错误）
apiCall('/jobs/page', 'POST', { page: 1, page_size: 5 })
apiCall('/jobs/page', 'POST', { page, page_size: 10 })

// 应改为
apiCall('/jobs/page', 'POST', { page_num: 1, page_size: 5 })
apiCall('/jobs/page', 'POST', { page_num: page, page_size: 10 })
```

**影响**: 岗位列表和首页最近岗位都无法正确分页，后端使用默认 `page_num=1`

---

### 8. 前端 — exportJobs 缺少请求体

**文件**: `app/static/index.html` 行 659-661  
**问题**: POST 请求 `/jobs/export` 没有发送 body，但后端期望 `JobQueryDTO` 请求体

```javascript
// 当前（错误）
const response = await fetch(`${API_BASE}/jobs/export`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
    // 缺少 body
});

// 应改为
const response = await fetch(`${API_BASE}/jobs/export`, {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({ page_num: 1, page_size: 999999 })
});
```

**影响**: 导出功能返回 422 Validation Error

---

## 🟡 警告（9 个）

### 9. log 路由 — `datetime.now()` 缺少时区

**文件**: `app/routers/log.py` 行 44  
**问题**: `datetime.now()` 无 timezone，项目其他地方统一用 `datetime.now(timezone.utc)`

```python
# 当前
cutoff = datetime.now() - timedelta(days=days)

# 应改为
from datetime import datetime, timezone
cutoff = datetime.now(timezone.utc) - timedelta(days=days)
```

---

### 10. log 路由 — clean_logs 低效删除

**文件**: `app/routers/log.py` 行 46-49  
**问题**: 先 SELECT 所有日志到内存，再逐条 `db.delete()`。数据量大时极其低效

```python
# 当前（低效）
stmt = select(SysLog).where(SysLog.created_at < cutoff)
result = await db.execute(stmt)
logs = result.scalars().all()
for log in logs:
    await db.delete(log)

# 应改为（批量删除）
from sqlalchemy import delete
stmt = delete(SysLog).where(SysLog.created_at < cutoff)
result = await db.execute(stmt)
await db.commit()
return Result.success({"deleted": result.rowcount})
```

---

### 11. knowledge 路由 — `/learn` 接口用 query 参数接收 POST 数据

**文件**: `app/routers/knowledge.py` 行 116-124  
**问题**: `question: str, answer: str, tags: str = ""` 作为查询参数，不符合 RESTful 规范

```python
# 当前
@router.post("/learn")
async def learn_knowledge(
    question: str,  # query param
    answer: str,    # query param
    tags: str = "",
    ...
):

# 应改为
class LearnRequest(BaseModel):
    question: str
    answer: str
    tags: str = ""

@router.post("/learn")
async def learn_knowledge(
    request: LearnRequest,
    ...
):
```

---

### 12. knowledge_service — 缓存存储 ORM 对象

**文件**: `app/services/knowledge_service.py` 行 33-39  
**问题**: `_cache["data"]` 直接存储 SQLAlchemy ORM 实例。Session 关闭后这些对象变为 detached 状态，访问属性可能触发 `DetachedInstanceError`

```python
# 当前（有风险）
items = result.scalars().all()
_cache["data"] = items  # ORM 对象，session 关闭后变 detached

# 建议改为存储纯数据
_cache["data"] = [{"id": i.id, "question": i.question, ...} for i in items]
# 或者干脆不用内存缓存，改用 Redis
```

---

### 13. ai_service — 流式请求超时太短

**文件**: `app/services/ai_service.py` 行 60  
**问题**: `httpx.AsyncClient(timeout=30)` 对 LLM 流式响应来说太短。GLM-4 长回答可能超过 30 秒

```python
# 当前
async with httpx.AsyncClient(timeout=30) as client:

# 建议改为（流式连接不设超时，或设更长）
async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10)) as client:
```

---

### 14. scheduler — 定时爬虫不应用数据过滤

**文件**: `app/scheduler.py` 行 47-68  
**问题**: 定时爬虫只做了 `deduplicate_jobs` 和 `clean_job_data`，但没有调用 `is_senior_job`、`is_invalid_job`、`is_high_salary` 过滤器。手动爬虫（`crawler_service.py`）有完整过滤，定时爬虫没有

**影响**: 定时爬虫会把高级岗位、无效岗位、高薪虚标岗位全部入库

---

### 15. scheduler — 定时爬虫不标记下架岗位

**文件**: `app/scheduler.py` 行 47-68  
**问题**: 定时爬虫不会将未出现的旧岗位标记为 OFFLINE，而手动爬虫（`crawler_service.py` 行 86-96）有此逻辑

---

### 16. ai_service — 模型降级时用户无感知

**文件**: `app/services/ai_service.py` 行 117-128  
**问题**: GLM-4 失败后静默切换到 Ollama，Ollama 失败后静默切换到规则引擎。用户看到的只是回答质量突然下降，不知道发生了什么

```python
# 建议在降级时发送一条提示
except Exception as e:
    logger.warning(f"GLM-4 failed: {e}")
    yield "⚠️ 云端模型暂时不可用，正在切换到本地模型...\n\n"
```

---

### 17. knowledge_service — `get_context_for_ai` 精确匹配几乎无效

**文件**: `app/services/knowledge_service.py` 行 122-128  
**问题**: 先用 `KnowledgeBase.question == keyword` 精确匹配用户的完整消息。用户输入的是自然语言（如"帮我分析一下薪资"），几乎不可能与知识库的 question 字段完全相等

**建议**: 直接走 LIKE 模糊匹配，跳过无用的精确匹配

---

## 🟢 建议（6 个）

### 18. log_decorator — 已定义但未使用

**文件**: `app/utils/log_decorator.py`  
**问题**: `@log_action` 装饰器定义了但没有任何路由使用它。操作日志模块虽然有了路由和模型，但实际操作时不会写入任何日志

**建议**: 在关键路由（create/update/delete）上加 `@log_action("创建岗位")` 等装饰器

---

### 19. cleaner — `parse_salary` 不支持"元/天""元/时"格式

**文件**: `app/crawlers/cleaner.py` 行 56-65  
**问题**: 只处理 `K`/`k` 后缀的薪资，不处理 BOSS 上常见的"元/天""元/时""元/月"格式

```python
# 建议增加
if "元/天" in salary_str or "/天" in salary_str:
    # 日薪转月薪（×22）
    match = re.match(r'(\d+)', salary_str)
    if match:
        daily = int(match.group(1))
        return daily * 22, daily * 22
```

---

### 20. cleaner — `is_high_salary` 返回非布尔值

**文件**: `app/crawlers/cleaner.py` 行 45  
**问题**: `return min_salary and min_salary > 30000` 当 min_salary=0 时返回 0 而非 False

```python
# 当前
def is_high_salary(min_salary: float) -> bool:
    return min_salary and min_salary > 30000

# 应改为
def is_high_salary(min_salary: float) -> bool:
    return bool(min_salary) and min_salary > 30000
```

---

### 21. base — `crawl_with_retry` 重试逻辑有缺陷

**文件**: `app/crawlers/base.py` 行 32-45  
**问题**: 第一次尝试如果部分成功（返回部分结果）后 break，不会重试。只有抛异常才重试

---

### 22. 缺少 `.env.example` 中的 `email-validator` 依赖说明

**文件**: `requirements.txt`  
**状态**: ✅ 已修复（上次审计指出，现已包含 `email-validator==2.2.0`）

---

### 23. boss.py — `hashlib.md5` 用于 job_id 回退

**文件**: `app/crawlers/boss.py` 行 217  
**问题**: `hashlib.md5((title + company_name).encode()).hexdigest()[:16]` 用 MD5 生成回退 job_id。虽然这里不是安全场景，但 MD5 有碰撞风险，建议统一用 sha256

---

## 修复优先级

```
P0（必须立即修）:
  1. knowledge.py from_orm → model_validate（8处）
  2. knowledge.py 响应格式 code:200 → Result.success()
  3. log.py SysLog 序列化失败 → 创建 SysLogResponse
  4. index.html 薪资预测中英文值不匹配
  5. index.html loadJobs page → page_num
  6. index.html exportJobs 缺少 body

P1（尽快修）:
  7. knowledge.py HTTPException → AppException
  8. log.py SQL LIKE 注入转义
  9. scheduler.py 补充数据过滤和下架标记
  10. ai_service.py 流式超时调大
  11. knowledge_service.py 缓存改用纯数据

P2（有空再修）:
  12. log_decorator.py 应用到路由
  13. cleaner.py 支持"元/天"格式
  14. knowledge_service.py 去掉无用精确匹配
  15. ai_service.py 降级时提示用户
```

---

## 进度对比

| 版本 | 文件数 | 代码行 | 🔴阻塞 | 🟡警告 | 完成度 |
|------|--------|--------|--------|--------|--------|
| V1 | 37 | 2193 | 10 | 11 | ~40% |
| V2 | 42 | 2206 | 5 | 6 | ~85% |
| V3 | 42 | 2206 | 1 | 3 | ~90% |
| V4 | 42 | 2206 | 2 | - | ~90% |
| **V5** | **49** | **3060** | **8** | **9** | **~95%** |

新增的 7 个文件补齐了功能短板，但引入了新的代码质量问题。knowledge 和 log 两个新模块需要重点修复。
