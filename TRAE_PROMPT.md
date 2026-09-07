# Trae 开发指令 — Recruitment Python 重构项目

项目位置就是这个文件夹。先读 TECH_SPEC.md 和 MIGRATION_GUIDE.md 了解完整背景。

---

## 第一步：先修复 3 个问题（5分钟）

### 1. database.py — 废弃API
`declarative_base()` 在 SQLAlchemy 2.0 已废弃，改成：
```python
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase):
    pass
```

### 2. security.py — datetime.utcnow() 废弃
把 `datetime.utcnow()` 改成 `datetime.now(timezone.utc)`，顶部加 `from datetime import timezone`。

### 3. routers/jobs.py — 消除重复代码
`/recommend` 和 `/recommend/intelligent` 两个接口各自有一份"经验年限字符串→数字"的映射（如 "1-3年"→2），跟 `analyzer.py` 的 `get_experience_years()` 重复。把 `/recommend` 里的映射逻辑删掉，直接调 `get_experience_years(experience)`。

---

## 第二步：AI 对话模块（核心）

新建 `app/services/ai_service.py` 和 `app/routers/ai.py`。

### GLM-4 API 对接
- 配置：`settings.zhipuai_api_key`、`settings.zhipuai_api_url`（默认 `https://open.bigmodel.cn/api/paas/v4/chat/completions`）
- 使用 `httpx.AsyncClient` 异步调用
- 请求体格式：
```json
{
  "model": "glm-4-flash",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true,
  "temperature": 0.7
}
```
- SSE 流式解析：响应是 `text/event-stream`，每行以 `data: ` 开头，JSON 解析 `choices[0].delta.content`
- 用 FastAPI `StreamingResponse` 返回，`media_type="text/event-stream"`
- 路由：`POST /api/ai/chat`，接收 `{ "message": "..." }`，返回 SSE 流

### Ollama 本地模型
- 配置：`settings.ollama_base_url`、`settings.ollama_model`（qwen2:7b）
- 用 httpx 调 `{ollama_base_url}/api/chat`
- 请求体：
```json
{
  "model": "qwen2:7b",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true
}
```
- 同样 SSE 流式解析
- 路由：`POST /api/ai/chat-local`，返回 SSE 流

### 双模型降级链
- `POST /api/ai/chat-stream`：先尝试 GLM-4，失败自动降级到 Ollama
- 把降级逻辑（try GLM-4 → except → try Ollama）写在 service 层

### 对话历史
- 不需要持久化到数据库，只用内存存储（`conversation_history: dict[str, list]` 按 session_id）
- `POST /api/ai/chat-stream` 接收 `{ "message": "...", "session_id": "..." }`，自动拼接最近 10 轮历史传入模型

---

## 第三步：爬虫模块

新建 `app/crawlers/` 包。

### 爬虫配置
- 配置项已存在于 `config.py`：`crawl_max_jobs=200`、`crawl_retry_times=4`、`crawl_delay_min=12`、`crawl_delay_max=18`、`crawl_timeout=45`

### 基类
`app/crawlers/base.py` — 抽象爬虫基类：
```python
class BaseCrawler(ABC):
    def __init__(self, source_site: str):
        self.source_site = source_site
    
    async def crawl(self, keyword: str, city: str = "") -> list[dict]:
        # 随机延时、指数退避重试、UA轮换
        pass
    
    @abstractmethod
    async def parse_page(self, page, keyword: str) -> list[dict]:
        pass
```

### BOSS直聘爬虫
- `app/crawlers/boss.py` — 继承 BaseCrawler
- 用 Playwright 启动无头浏览器，访问 `https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}`
- 用 BS4 解析岗位卡片：标题、薪资、公司、城市、经验、学历、技能标签
- 翻页爬取，上限 `crawl_max_jobs` 条
- 反爬：随机延时 12-18 秒、指数退避重试（2^1, 2^2, 2^3, 2^4 秒）、UA 轮换（准备 10 个常见 UA）
- `app/crawlers/city_map.py` — BOSS直聘城市代码映射表（北京→101010100 等，至少覆盖北上广深杭成武南长）

### 数据清洗
`app/crawlers/cleaner.py`：
- 薪资：把 "8K-12K" 解析成 min_salary=8000, max_salary=12000
- 去重：根据 job_key（source_site+岗位ID）去重
- 技能提取：从岗位描述中用正则提取常见技能关键词

### 爬虫路由
`app/routers/crawler.py`：
- `POST /api/crawler/start` — 接收 `{ "keyword": "Java", "city": "长沙", "platforms": ["boss"] }`，异步启动爬虫任务
- `GET /api/crawler/tasks` — 查看爬虫任务状态（读 crawl_task 表）
- `GET /api/crawler/tasks/{task_id}` — 查看单个任务详情

### 定时任务
- 在 `app/main.py` 中注册 APScheduler
- 每天凌晨 2 点自动爬取 "Java"、"Python"、"前端" 三个关键词
- `app/scheduler.py`

---

## 第四步：全局异常处理

新建 `app/exceptions.py`：

```python
from fastapi import Request
from fastapi.responses import JSONResponse

class AppException(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code

async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "message": exc.message, "data": None})

async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"code": 500, "message": "服务器内部错误", "data": None})
```

在 `main.py` 的 `create_app()` 中注册：
```python
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
```

然后把现有路由里所有的 `raise HTTPException(...)` 改成 `raise AppException(...)`。

---

## 第五步：Excel 导入导出

### 导出
`app/services/export_service.py`：
- `export_jobs_to_excel(jobs: list[Job])` → 生成 Excel 文件，返回 `StreamingResponse`
- 路由：`POST /api/jobs/export`，接收 `JobQueryDTO`，导出符合筛选条件的岗位
- 列：标题、公司、城市、薪资(min)、薪资(max)、经验、学历、技能、来源平台、发布时间

### 导入
- 路由：`POST /api/jobs/import`，接收 Excel 文件上传
- 用 openpyxl 逐行读取，批量写入数据库
- 第 1 行是表头，从第 2 行开始读
- 返回成功/失败数量

---

## 完成后检查清单

- [ ] `uvicorn app.main:app --port 8080` 能正常启动
- [ ] `POST /api/auth/login` 能登录
- [ ] `POST /api/ai/chat-stream` 能返回 SSE 流（GLM-4 或 Ollama 至少一个）
- [ ] `POST /api/crawler/start` 能启动爬虫（需要装 Playwright 并 `playwright install chromium`）
- [ ] `POST /api/jobs/export` 能下载 Excel
- [ ] 不存在的路由返回统一 JSON 错误，不是 HTML 404

---

## 注意
- 数据库不要动，复用现有的 MySQL `recruitment_db`
- 用 `httpx.AsyncClient` 做所有 HTTP 调用（不要用 requests 库，它是同步的会阻塞事件循环）
- 所有新增路由统一用 `Result` 包装返回值
- 写完一个模块先跑通，再做下一个，不要一次全写完
