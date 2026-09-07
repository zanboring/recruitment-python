# Recruitment Python 项目审计报告 V6

**审计日期**: 2026-07-07  
**项目规模**: 50 文件 / 3117 行 Python + 1 HTML  
**语法检查**: ✅ 全部通过

---

## 总体评价

上次 V5 审计的 **8 个阻塞级问题全部已修复** ✅，knowledge 和 log 两个新模块的代码质量显著提升。

但本轮审计发现 **2 个新的阻塞级问题**和 **5 个警告级问题**，主要集中在 knowledge_service 和前端。

### V5 修复情况

| V5 问题 | 状态 |
|---------|------|
| knowledge.py from_orm → model_validate（8处） | ✅ 已修 |
| knowledge.py 响应格式 code:200 → Result.success() | ✅ 已修 |
| knowledge.py HTTPException → AppException | ✅ 已修 |
| log.py SysLog 序列化 → SysLogResponse | ✅ 已修 |
| log.py SQL LIKE 注入 → _escape_like | ✅ 已修 |
| 前端薪资预测中英文值不匹配 | ✅ 已修 |
| 前端 loadJobs page → page_num | ✅ 已修 |
| 前端 exportJobs 缺少 body | ✅ 已修 |
| log.py datetime.now() 无时区 | ✅ 已修 |
| log.py 低效删除 → 批量 delete | ✅ 已修 |

---

## 🔴 阻塞级问题（2 个）

### 1. knowledge_service — `learn_from_response()` 运行时崩溃

**文件**: `app/services/knowledge_service.py` 行 164-172  
**问题**: `question.like(...)` 调用 Python 字符串的 `.like()` 方法，但 `str` 类型没有此方法，运行时抛出 `AttributeError`

```python
# 当前（崩溃）
stmt = select(KnowledgeBase).where(
    or_(
        KnowledgeBase.question.like(f"%{question}%"),
        question.like(f"%{KnowledgeBase.question}%")  # ← str 没有 .like() 方法
    )
)
```

**调用链**: `ai_service.call_chat_stream()` → GLM-4 成功输出后 → `learn_from_response(db, message, response_content)` → 崩溃

**影响**: 
- 知识库自动学习功能完全失效——每次 AI 对话成功后尝试学习新知识时都会崩溃
- 异常被 `call_chat_stream` 的 `except Exception` 捕获后，会误降级到 Ollama，导致用户收到 GLM-4 回答 + Ollama 重复回答
- `question.like(f"%{KnowledgeBase.question}%")` 即使能执行，`f"%{KnowledgeBase.question}%"` 也只是把 Column 对象转为字符串 `%knowledge_base.question%`，毫无意义

**修复**:
```python
# 方案1：直接删除这段无效的模糊查重，只保留精确查重
# learn_from_response 的目的是避免重复学习，精确匹配已足够
async def learn_from_response(db: AsyncSession, question: str, answer: str, tags: str = ""):
    stmt = select(KnowledgeBase).where(KnowledgeBase.question == question)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        return

    # 直接创建，不做模糊查重
    if not tags:
        tags = ",".join([kw for kw in KEYWORDS if kw in question or kw in answer])
    ...
```

---

### 2. 前端 — `searchJobs()` 搜索条件未传递

**文件**: `app/static/index.html` 行 499-503  
**问题**: 读取了 keyword 和 city 但没有传给 `loadJobs()`，搜索功能完全无效

```javascript
// 当前（无效）
function searchJobs() {
    const keyword = document.getElementById('job-search').value;
    const city = document.getElementById('job-city').value;
    loadJobs(1);  // ← 没有传 keyword 和 city
}

// loadJobs 发送的请求体
async function loadJobs(page = 1) {
    currentPage = page;
    const result = await apiCall('/jobs/page', 'POST', { page_num: page, page_size: 10 });
    // ← 不包含 keyword 和 city
}
```

**影响**: 用户在搜索框输入关键词或选择城市后点搜索，返回的仍然是全量分页数据，搜索功能形同虚设

**修复**:
```javascript
let currentKeyword = '';
let currentCity = '';

function searchJobs() {
    currentKeyword = document.getElementById('job-search').value;
    currentCity = document.getElementById('job-city').value;
    loadJobs(1);
}

async function loadJobs(page = 1) {
    currentPage = page;
    const body = { page_num: page, page_size: 10 };
    if (currentKeyword) body.keyword = currentKeyword;
    if (currentCity) body.city = currentCity;
    const result = await apiCall('/jobs/page', 'POST', body);
    ...
}
```

---

## 🟡 警告（5 个）

### 3. crawler_service — `datetime.now()` 无时区（2 处）

**文件**: `app/services/crawler_service.py` 行 3, 17, 33  
**问题**: 只导入了 `datetime`，未导入 `timezone`，使用了无时区的 `datetime.now()`

```python
# 当前
from datetime import datetime          # 行 3 — 缺少 timezone
job.last_seen_at = datetime.now()      # 行 17 — 无时区
last_seen_at=datetime.now(),           # 行 33 — 无时区

# 应改为
from datetime import datetime, timezone
job.last_seen_at = datetime.now(timezone.utc)
last_seen_at=datetime.now(timezone.utc),
```

**影响**: 与项目其他模块（auth_service、log、security）统一使用 `timezone.utc` 不一致，数据库时间戳混乱

---

### 4. knowledge_service — `search()` LIKE 注入未转义

**文件**: `app/services/knowledge_service.py` 行 44-49  
**问题**: 用户搜索关键词直接拼入 LIKE 通配符，无转义

```python
# 当前（有风险）
KnowledgeBase.question.like(f"%{keyword}%"),
KnowledgeBase.tags.like(f"%{keyword}%"),
KnowledgeBase.answer.like(f"%{keyword}%")
```

**影响**: log.py 和 job_service.py 都已修复 LIKE 注入，但 knowledge_service 的 search() 遗漏了

**修复**: 导入 `app.services.job_service._escape_like` 或在本地定义转义函数

---

### 5. knowledge_service — `get_context_for_ai()` LIKE 注入未转义

**文件**: `app/services/knowledge_service.py` 行 131-137  
**问题**: 同上，AI 上下文检索的模糊匹配也未转义

```python
# 当前（有风险）
KnowledgeBase.question.like(f"%{keyword}%"),
KnowledgeBase.tags.like(f"%{keyword}%"),
KnowledgeBase.answer.like(f"%{keyword}%")
```

---

### 6. analyzer — `recommend_jobs()` LIKE 注入未转义

**文件**: `app/recommender/analyzer.py` 行 97-98  
**问题**: 用户输入的技能关键词直接拼入 LIKE

```python
# 当前（有风险）
for keyword in skill_keywords:
    or_conditions.append(Job.skills.like(f"%{keyword}%"))
    or_conditions.append(Job.title.like(f"%{keyword}%"))
```

**影响**: 用户在推荐接口输入 `%` 或 `_` 会导致意外匹配

---

### 7. 前端 — `predictSalary` URL 未编码 + 城市下拉框未动态加载

**文件**: `app/static/index.html`  
**问题 A** (行 645): skills 参数含特殊字符时 URL 未编码

```javascript
// 当前（有风险）
const result = await apiCall(
    `/jobs/predict-salary?city=${city}&education=${education}&experience=${experience}&skills=${skills}`
);

// 应改为
const params = new URLSearchParams({ city, education, experience, skills });
const result = await apiCall(`/jobs/predict-salary?${params}`);
```

**问题 B** (行 176-178): 岗位管理页面的城市筛选下拉框只有"全部城市"一个选项，没有从后端动态加载城市列表

```html
<!-- 当前 -->
<select id="job-city">
    <option value="">全部城市</option>
    <!-- 没有其他选项 -->
</select>
```

---

## V5 遗留未修问题（7 警告 + 5 建议）

以下问题在 V5 报告中已提出，本轮仍未修复：

### 🟡 遗留警告（7 个）

| # | 文件 | 问题 |
|---|------|------|
| 8 | knowledge.py 行 122-125 | `/learn` 接口用 query 参数接收 POST 数据 |
| 9 | knowledge_service.py 行 38 | 缓存存储 ORM 对象，session 关闭后变 detached |
| 10 | ai_service.py 行 60 | 流式请求 timeout=30 太短 |
| 11 | scheduler.py 行 47-68 | 定时爬虫不做数据过滤（is_senior_job 等） |
| 12 | scheduler.py 行 47-68 | 定时爬虫不标记下架岗位 |
| 13 | ai_service.py 行 117-128 | 模型降级时用户无感知 |
| 14 | knowledge_service.py 行 122-128 | get_context_for_ai 精确匹配几乎无效 |

### 🟢 遗留建议（5 个）

| # | 文件 | 问题 |
|---|------|------|
| 15 | log_decorator.py | @log_action 装饰器已定义但未使用 |
| 16 | cleaner.py 行 56-65 | parse_salary 不支持"元/天""元/时"格式 |
| 17 | cleaner.py 行 45 | is_high_salary 返回非布尔值（min_salary=0 时返回 0） |
| 18 | base.py 行 32-45 | crawl_with_retry 部分成功不重试 |
| 19 | boss.py 行 217 | hashlib.md5 用于 job_id 回退（建议 sha256） |

---

## 修复优先级

```
P0（必须立即修）:
  1. knowledge_service.py learn_from_response question.like() 崩溃
  2. 前端 searchJobs() 搜索条件未传递

P1（尽快修）:
  3. crawler_service.py datetime.now() → datetime.now(timezone.utc)
  4. knowledge_service.py search() LIKE 注入转义
  5. knowledge_service.py get_context_for_ai() LIKE 注入转义
  6. analyzer.py recommend_jobs() LIKE 注入转义
  7. 前端 predictSalary URL 编码 + 城市下拉框动态加载

P2（有空再修）:
  8-14. V5 遗留警告
  15-19. V5 遗留建议
```

---

## 进度对比

| 版本 | 文件数 | 代码行 | 🔴阻塞 | 🟡警告 | 完成度 |
|------|--------|--------|--------|--------|--------|
| V1 | 37 | 2193 | 10 | 11 | ~40% |
| V2 | 42 | 2206 | 5 | 6 | ~85% |
| V3 | 42 | 2206 | 1 | 3 | ~90% |
| V4 | 42 | 2206 | 2 | - | ~90% |
| V5 | 49 | 3060 | 8 | 9 | ~95% |
| **V6** | **50** | **3117** | **2** | **5+7** | **~96%** |

V5 的 8 个阻塞全部修复，代码质量明显提升。本轮仅剩 2 个新阻塞（1 个后端运行时崩溃 + 1 个前端功能缺失）和 5 个新警告。项目整体趋于稳定。
