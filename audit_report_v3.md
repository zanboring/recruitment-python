# Recruitment Python 项目第三次全面审计报告

> 审计时间：2026-07-06  
> 项目：C:/Users/zanboring/OneDrive - Ormesby Primary/Desktop/recruitment-python/  
> 代码量：42 个 Python 文件，共 2,193 行（语法检查：全部通过 ✅）

---

## 一、与前两次审计的对比

| 维度 | V1 (首次) | V2 (第二次) | V3 (本次) |
|------|----------|------------|----------|
| 文件数 | 30 | 37 | 42 |
| 代码量 | ~1800 行 | ~2400 行 | 2193 行 |
| 阻塞问题 | 🔴 10 | 🔴 5 | 🔴 1 |
| 警告 | 🟡 6 | 🟡 11 | 🟡 7 |
| 功能完成度 | ~40% | ~85% | ~90% |

### V2 报告的 5 个阻塞问题修复情况

| V2 问题 | 状态 |
|---------|------|
| 1. `from_orm()` / `class Config` | ✅ 已修复 — 全部改为 `model_config = {"from_attributes": True}` |
| 2. `create_job` 中无用的 `db.get` | ✅ 已修复 — 改为 `select(Job).where(Job.job_key == job_key)` |
| 3. 导出只导 20 条 | ✅ 已修复 — `export_jobs_to_excel` 中设置了 `page_size = 999999` |
| 4. 多 worker 定时任务重复 | ⚠️ 已加 `coalesce=True, max_instances=1`，但多进程部署仍有风险 |
| 5. 缺少用户管理路由 | ✅ 已修复 — 新增 `app/routers/user.py`（71行，5个端点）|

---

## 二、🔴 阻塞级问题（1 个）

### 1. BOSS 直聘爬虫用 httpx 抓取 JS 渲染页面 — 无法正常工作

**文件**：`app/crawlers/boss.py:21-47`  
**严重程度**：🔴 致命 — 功能完全不可用

**问题**：
```python
# boss.py line 27-34
url = f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}&page={page}"
async with httpx.AsyncClient(...) as client:
    response = await client.get(url)
    page_results = await self.parse_page(response.text, keyword)
```

BOSS 直聘是高度 JS 渲染的 SPA（单页应用），初始 HTML 返回的是空壳 + 反爬验证页。httpx 只能拿到静态 HTML，**无法获取 JS 动态加载的岗位列表**。`BeautifulSoup` 解析的 `div.job-card`、`span.job-name` 等元素根本不存在于初始 HTML 中。

**影响**：爬虫模块形同虚设，手动触发爬虫或定时任务都不会有实际产出。

**修复**：`requirements.txt` 已经装了 `playwright==1.49.0`，但没有使用。应改为：

```python
from playwright.async_api import async_playwright

async def crawl(self, keyword: str, city: str = "") -> List[Dict]:
    results = []
    city_code = get_city_code(city)
    page_num = 1

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=self.get_random_ua(),
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        while len(results) < settings.crawl_max_jobs:
            url = f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}&page={page_num}"
            try:
                await page.goto(url, timeout=settings.crawl_timeout * 1000)
                await page.wait_for_selector(".job-card", timeout=10000)
                html = await page.content()
                page_results = await self.parse_page(html, keyword)
                if not page_results:
                    break
                results.extend(page_results)
                await self.random_delay()
                page_num += 1
            except Exception as e:
                logger.error(...)
                break

        await browser.close()
    return results
```

---

## 三、🟡 警告（7 个）

### 1. `Result` 响应格式不一致 — `msg` vs `message`

**文件**：
- `app/schemas/common.py:9` — `msg: str = "success"`
- `app/exceptions.py:13` — `content={"code": exc.code, "message": exc.message, ...}`

**问题**：成功响应用 `msg` 字段，异常响应用 `message` 字段。前端需要判断两个不同的 key，容易出错。

**修复**：统一为 `message`。

```python
# common.py
class Result(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"  # was: msg
    data: Optional[T] = None
```

---

### 2. 爬虫中 `hash()` 用于生成 job_key — 不稳定

**文件**：`app/crawlers/boss.py:83`

```python
job_id = job_id_match.group(1) if job_id_match else str(hash(title + company_name))
```

**问题**：Python 3 的 `hash()` 函数自 3.3 起默认启用 [PYTHONHASHSEED 随机化](https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED)，**每次进程启动时种子不同**，同一字符串的 `hash()` 值每次运行都不一样。这会导致：
- 同一条岗位在不同爬取批次中被识别为不同记录
- 数据库中产生大量重复

**修复**：使用 `hashlib` 替代：

```python
import hashlib
job_id = job_id_match.group(1) if job_id_match else hashlib.md5((title + company_name).encode()).hexdigest()[:16]
```

---

### 3. `SysLog` 模型无人写入 — 死代码

**文件**：`app/models/sys_log.py`  
**问题**：模型定义完整（username、action、method、uri、ip 等），但在整个项目中没有任何地方 `db.add(SysLog(...))`。等于没有操作日志功能。

**建议**：要么实现一个 FastAPI 中间件（`@app.middleware("http")`）自动记录请求日志，要么删除这个模型避免误导。

---

### 4. `UserInfoResponse` 和 `UserResponse` 高度重复

**文件**：
- `app/schemas/auth.py:20-27` — `UserInfoResponse`（7个字段）
- `app/schemas/user.py:11-22` — `UserResponse`（10个字段，多了 `login_fail_count` 和 `created_at`）

**问题**：两个 Schema 的 7 个基础字段完全重复。`UserInfoResponse` 用于认证相关接口，`UserResponse` 用于用户管理接口。维护两套但功能重叠。

**建议**：让 `UserResponse` 继承 `UserInfoResponse`：

```python
class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    skills: str | None
    education: str | None
    experience_years: int | None
    model_config = {"from_attributes": True}

class UserResponse(UserInfoResponse):
    login_fail_count: int
    created_at: str
```

---

### 5. 推荐引擎全量加载 — 性能隐患

**文件**：`app/recommender/analyzer.py:86-91`

```python
stmt = select(Job).where(Job.job_status == "ACTIVE")
result = await db.execute(stmt)
jobs = result.scalars().all()  # 所有活跃岗位全部加载到内存
```

**问题**：如果有 10 万条岗位，每次都全量加载再计算 Jaccard 相似度，内存和响应时间都会崩。虽然现在数据量小（几百条），但这个设计不可扩展。

**建议**：先按城市预过滤（如果指定了城市），再按技能关键词用 SQL LIKE 做粗筛，缩小候选集到几百条再精排。

---

### 6. Excel 导入不设置 `job_desc` 字段

**文件**：`app/services/export_service.py:81-93`

```python
job = Job(
    title=...,
    company_name=...,
    city=...,
    # ... 其他字段
    job_status="ACTIVE",
)
# ❌ job_desc 未设置
```

**问题**：导入的岗位没有职位描述，导致搜索（`Job.job_desc.like(...)`）和 AI 问答都查不到这些岗位的内容。

**修复**：在 `headers` 列表和 Excel 模板中加入"描述"列，并在 `Job(...)` 构造中设置 `job_desc`。

---

### 7. `routers/crawler.py` 任务列表序列化逻辑重复

**文件**：`app/routers/crawler.py:36-46` 和 `:51-65`

`list_tasks`（`GET /tasks`）和 `get_task_detail`（`GET /tasks/{task_id}`）中的 `CrawlTask` → dict 转换逻辑完全重复。

**建议**：抽取为公共函数或在 `CrawlTask` 模型中添加 `to_dict()` 方法。

---

## 四、🟢 建议（4 个）

| # | 文件 | 问题 | 建议 |
|---|------|------|------|
| 1 | `crawlers/cleaner.py:1 & 29` | `import re` 重复出现两次 | 删除第 29 行的冗余 import |
| 2 | `services/job_service.py:280` | `sha256().digest()[:43].hex()` 不直观 | 改用 `sha256().hexdigest()`（64位标准输出） |
| 3 | `schemas/user.py:20` | `created_at: str` 靠 Pydantic 隐式转换 | 显式添加 `field_serializer` 和类型注解，提高可读性 |
| 4 | `scheduler.py:84-86` | 多进程部署时每个 worker 各自启动 scheduler | 生产环境建议用独立进程运行 scheduler，或至少加环境变量开关 |

---

## 五、总结

### 变化（V2 → V3）

Trae 这次认真地修了大部分问题。**V2 的 5 个 🔴 阻塞修了 4 个**，新增了用户管理模块。代码质量整体上了一个台阶。

### 剩余工作

| 优先级 | 数量 | 核心问题 |
|--------|------|---------|
| 🔴 必须修 | 1 | BOSS 爬虫 httpx → Playwright（不用 Playwright 爬虫就废了） |
| 🟡 建议修 | 7 | 响应格式不一致、hash()不稳定、死代码、重复逻辑、性能隐患 |
| 🟢 可后补 | 4 | 代码风格、冗余 import |

### 一句话给 Trae

> "BOSS 爬虫必须改用 Playwright（httpx 抓不到 JS 渲染的岗位列表）；把 `hash()` 换成 `hashlib.sha256()`（Python hash 值不稳定会导致重复数据）；统一前后端响应字段 `msg` → `message`。这三个修完项目就能正经跑了。"
