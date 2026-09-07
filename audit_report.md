# Recruitment Python 项目审计报告

## 项目概览

- **总文件数**: 28 个 Python 文件 + 3 个配置文件 (.env, .env.example, requirements.txt)
- **Python 文件清单**:
  - `app/__init__.py`, `app/main.py`, `app/config.py`, `app/database.py`, `app/dependencies.py`, `app/exceptions.py`, `app/scheduler.py`
  - `app/models/`: `__init__.py`, `user.py`, `job.py`, `company.py`, `crawl_task.py`, `knowledge_base.py`, `sys_log.py`
  - `app/schemas/`: `__init__.py`, `common.py`, `auth.py`, `user.py`, `job.py`
  - `app/routers/`: `__init__.py`, `auth.py`, `jobs.py`, `crawler.py`, `ai.py`（无 `user.py`）
  - `app/services/`: `__init__.py`, `auth_service.py`, `job_service.py`, `ai_service.py`, `crawler_service.py`, `export_service.py`
  - `app/recommender/`: `__init__.py`, `jaccard.py`, `salary_predictor.py`, `analyzer.py`
  - `app/utils/`: `__init__.py`, `security.py`
  - `app/crawlers/`: `__init__.py`, `base.py`, `boss.py`, `cleaner.py`, `city_map.py`

- **功能完成度评估（对照 TRAE_PROMPT.md 5个阶段）**:

| 阶段 | 描述 | 完成状态 | 备注 |
|------|------|----------|------|
| 第一步 | 修复3个基础问题 | **90%** | database.py 已用 DeclarativeBase；security.py 已用 `datetime.now(timezone.utc)`；jobs.py `/recommend` 已调用 `get_experience_years()`，但 `.from_orm()` 未修复 |
| 第二步 | AI对话模块 | **95%** | ai_service.py、ai.py 路由已完成，GLM-4/Ollama/降级链/SSE 均已实现；对话历史用内存存储已实现 |
| 第三步 | 爬虫模块 | **85%** | BaseCrawler、BossCrawler、cleaner、city_map 基本完成；但 `time.sleep()` 同步阻塞是严重问题；scheduler.py 异常吞没 |
| 第四步 | 全局异常处理 | **80%** | AppException + 三个 handler 已实现并注册到 main.py；但路由层仍大量使用 `raise AppException` 而不是完全替换 HTTPException；dependencies.py 还在用 HTTPException |
| 第五步 | Excel导入导出 | **70%** | 导出基本完成；导入接口定义有问题（`file: bytes` 参数不正确）；export_service.py 缺少 `datetime` 导入但实际使用了 |

---

## 🔴 阻塞级问题

### 1. Pydantic v2 `.from_orm()` 废弃方法 — 全项目 7 处

- **文件**: `app/services/auth_service.py:36,60,72` + `app/services/job_service.py:45,85,109,152`
- **问题描述**: Pydantic v2 已移除 `.from_orm()` 方法，必须使用 `.model_validate()`。当前项目使用 pydantic==2.9.0，`.from_orm()` 在 Pydantic v2 中已被标记为废弃并将在未来版本完全移除。虽然 schema 定义中已正确使用 `class Config: from_attributes = True`，但调用方式不对。
- **当前代码**:
  ```python
  # auth_service.py:36
  user_info = UserInfoResponse.from_orm(user)
  # auth_service.py:60
  return UserInfoResponse.from_orm(user)
  # auth_service.py:72
  return UserInfoResponse.from_orm(user)
  # job_service.py:45
  return JobResponse.from_orm(job)
  # job_service.py:85
  return JobResponse.from_orm(job)
  # job_service.py:109
  return JobResponse.from_orm(job)
  # job_service.py:152
  return [JobResponse.from_orm(job) for job in jobs]
  ```
- **修复建议**: 全部替换为 `UserInfoResponse.model_validate(user)` 和 `JobResponse.model_validate(job)`。

---

### 2. 异步函数中的同步阻塞调用 `time.sleep()`

- **文件**: `app/crawlers/base.py:30,42`
- **问题描述**: `random_delay()` 和 `crawl_with_retry()` 在 async 方法中使用 `time.sleep()`，会阻塞整个事件循环，导致所有并发请求挂起。BossCrawler.crawl() 调用 `self.random_delay()`（第37行），在异步爬虫流程中造成严重阻塞。
- **当前代码**:
  ```python
  # base.py:30
  def random_delay(self):
      delay = random.uniform(settings.crawl_delay_min, settings.crawl_delay_max)
      time.sleep(delay)

  # base.py:42
  time.sleep(wait_time)
  ```
- **修复建议**:
  ```python
  import asyncio
  async def random_delay(self):
      delay = random.uniform(settings.crawl_delay_min, settings.crawl_delay_max)
      await asyncio.sleep(delay)

  # crawl_with_retry 中:
  await asyncio.sleep(wait_time)
  ```
  同时将 `crawl_with_retry` 和调用 `self.random_delay()` 的地方改为 `await self.random_delay()`。

---

### 3. JWT_SECRET 为占位符，非真实安全值

- **文件**: `.env:7`
- **问题描述**: `JWT_SECRET=your_jwt_secret_base64_that_is_at_least_32_characters_long` 是明显占位符，不是真实密钥。在生产环境中使用此值意味着任何知道此字符串的人都可以伪造 JWT token，完全绕过认证。
- **当前代码**:
  ```
  JWT_SECRET=your_jwt_secret_base64_that_is_at_least_32_characters_long
  ```
- **修复建议**: 生成真实的高强度随机密钥，如 `openssl rand -base64 32`。config.py 中 `jwt_secret: str` 没有 default 值，如果 .env 中保持占位符字符串虽然不会启动报错，但安全性为零。

---

### 4. scheduler.py 异常被完全吞没

- **文件**: `app/scheduler.py:68-69`
- **问题描述**: 定时爬虫的 `scheduled_crawl()` 中 `except Exception as e: pass` 完全吞没了所有异常，包括数据库连接失败、爬虫失败等。任务状态停留在 "RUNNING" 永远不会变成 "FAILED"，且没有任何日志记录。
- **当前代码**:
  ```python
  except Exception as e:
      pass
  ```
- **修复建议**:
  ```python
  except Exception as e:
      task.status = "FAILED"
      task.message = str(e)
      await db.commit()
      import logging
      logging.getLogger("scheduler").error(f"Scheduled crawl failed: {e}", exc_info=True)
  ```

---

### 5. scheduler.py 使用 `__import__()` 动态导入 SQLAlchemy

- **文件**: `app/scheduler.py:41`
- **问题描述**: 使用 `__import__('sqlalchemy').select(Job)` 是非常不规范的做法，硬编码动态导入难以维护、调试，且 IDE 无法识别。
- **当前代码**:
  ```python
  existing = await db.execute(
      __import__('sqlalchemy').select(Job).where(Job.job_key == cleaned["job_key"])
  )
  ```
- **修复建议**: 文件顶部已有 `from sqlalchemy import select` 的导入条件（在函数内部延迟导入），应该把它加到函数顶部的导入块中：
  ```python
  from sqlalchemy import select as sa_select
  ```
  然后使用 `sa_select(Job).where(Job.job_key == cleaned["job_key"])`。

---

### 6. import 接口参数定义错误 — 无法接收上传文件

- **文件**: `app/routers/jobs.py:199`
- **问题描述**: `import_jobs` 路由的 `file: bytes` 参数声明不正确。FastAPI 文件上传应使用 `UploadFile` 类型（来自 `fastapi import UploadFile`），`bytes` 类型不会自动从请求体中读取文件上传。
- **当前代码**:
  ```python
  @router.post("/import")
  async def import_jobs(
      file: bytes,
      db: AsyncSession = Depends(get_db),
      user: User = Depends(require_admin)
  ):
  ```
- **修复建议**:
  ```python
  from fastapi import UploadFile

  @router.post("/import")
  async def import_jobs(
      file: UploadFile,
      db: AsyncSession = Depends(get_db),
      user: User = Depends(require_admin)
  ):
      file_content = await file.read()
      result = await import_jobs_from_excel(db, file_content)
      return Result.success(result)
  ```

---

### 7. CORS 配置 `.env` 中是 JSON 字符串而非列表

- **文件**: `.env:24`
- **问题描述**: `CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]` 在 .env 中是 JSON 字符串格式。Pydantic-settings 会将其解析为字符串而非列表，导致 `settings.cors_origins` 类型为 `str`，传给 `CORSMiddleware` 后会出错（allow_origins 期望字符串列表）。
- **当前代码**:
  ```
  CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]
  ```
- **修复建议**: `.env` 中改为逗号分隔格式（与 `.env.example` 一致）：
  ```
  CORS_ORIGINS=http://localhost:5173,http://localhost:3000
  ```
  同时在 `config.py` 中确保 `cors_origins` 使用 pydantic-settings 的自动逗号分隔解析。验证方式：`pydantic-settings` 默认会将逗号分隔字符串解析为 `list[str]`。但 JSON 字符串 `["..."]` 不会被自动解析为列表，需要额外配置。

---

### 8. dependencies.py 仍使用 HTTPException，与全局异常体系不一致

- **文件**: `app/dependencies.py:19,23,30`
- **问题描述**: TRAE_PROMPT.md 第四步要求将所有 `HTTPException` 替换为 `AppException`，但 dependencies.py 中仍然使用 `raise HTTPException(status_code=401/403, detail="...")`。虽然 main.py 注册了 `http_exception_handler` 处理 StarletteHTTPException（会包装为统一格式），但这违反了项目的统一异常设计意图，且 HTTPException 返回的格式是 `{"code": 401, "message": "请先登录", "data": None}`，而 AppException 返回格式相同，风格不一致但不致命。更严重的是 HTTPException 的 detail 字段可能被 FastAPI 默认 handler 拦截后再被自定义 handler 处理，行为不够明确。
- **当前代码**:
  ```python
  raise HTTPException(status_code=401, detail="请先登录")
  raise HTTPException(status_code=401, detail="用户不存在")
  raise HTTPException(status_code=403, detail="权限不足")
  ```
- **修复建议**: 替换为 `AppException`：
  ```python
  from app.exceptions import AppException
  raise AppException("请先登录", 401)
  raise AppException("用户不存在", 401)
  raise AppException("权限不足", 403)
  ```

---

### 9. export_service.py 使用 `datetime.now()` 但无 timezone

- **文件**: `app/services/export_service.py:53`
- **问题描述**: 导出文件名使用 `datetime.now().strftime(...)` 但没有指定 timezone，与 security.py 已修复的做法不一致。在服务器部署时可能出现时区偏差。
- **当前代码**:
  ```python
  filename = f"jobs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
  ```
- **修复建议**:
  ```python
  filename = f"jobs_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
  ```
  并在顶部添加 `from datetime import timezone`。

---

### 10. auth_service.py 使用 `datetime.now()` 无 timezone，与锁定逻辑时区不一致

- **文件**: `app/services/auth_service.py:20-21,27`
- **问题描述**: 登录锁定检查使用 `datetime.now()` 但不带 timezone，而 `user.locked_until` 列类型是 `DateTime`（无 timezone 信息），MySQL 存储的值取决于服务器时区设置。如果 MySQL 用 UTC，而 Python 用本地时间，比较会出错。
- **当前代码**:
  ```python
  if user.locked_until and user.locked_until > datetime.now():
      remaining = (user.locked_until - datetime.now()).total_seconds() // 60
  user.locked_until = datetime.now() + timedelta(minutes=30)
  ```
- **修复建议**: 统一使用 `datetime.now(timezone.utc)` 或确保 MySQL 连接配置了正确时区参数。如果 MySQL 时区为 Asia/Shanghai，则应使用 `datetime.now()` 本地时间（需确认一致性）。最安全的做法是在 DATABASE_URL 中添加 `&timezone=Asia/Shanghai` 参数，并在代码中使用对应时区。

---

## 🟡 警告

### 1. scheduler.py 在模块级启动 — 启动时序风险

- **文件**: `app/main.py:46-47`
- **问题描述**: `from app.scheduler import start_scheduler` 和 `start_scheduler()` 在模块级别执行，在 `app = create_app()` 之后立即调用。这意味着导入 app.main 模块就会启动 scheduler，测试、迁移等场景可能无意触发定时任务。
- **当前代码**:
  ```python
  app = create_app()

  from app.scheduler import start_scheduler
  start_scheduler()
  ```
- **修复建议**: 将 scheduler 启动放在 lifespan 事件中：
  ```python
  from contextlib import asynccontextmanager

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      from app.scheduler import start_scheduler
      start_scheduler()
      yield
      from app.scheduler import scheduler
      scheduler.shutdown()

  app = FastAPI(lifespan=lifespan, ...)
  ```

---

### 2. conversation_history 内存存储无上限清理机制

- **文件**: `app/services/ai_service.py:9`
- **问题描述**: `conversation_history: dict[str, list]` 是纯内存存储，没有任何清理机制。随着时间推移，session_id 越来越多，内存会无限增长。虽然单个 session 限制了 20 条，但 session 数量本身没有上限。
- **当前代码**:
  ```python
  conversation_history: dict[str, list] = {}
  ```
- **修复建议**: 添加定期清理或最大 session 数限制：
  ```python
  MAX_SESSIONS = 1000

  def update_conversation_history(session_id, user_message, ai_response):
      if len(conversation_history) >= MAX_SESSIONS:
          # 清理最旧的 session
          oldest = list(conversation_history.keys())[0]
          conversation_history.pop(oldest, None)
      ...
  ```

---

### 3. CrawlRequest.platforms 类型为 `list`，过于宽泛

- **文件**: `app/routers/crawler.py:18`
- **问题描述**: `platforms: list = ["boss"]` 使用裸 `list` 类型，没有指定元素类型。Pydantic v2 要求明确类型注解，裸 `list` 会接受任意类型元素。
- **当前代码**:
  ```python
  class CrawlRequest(BaseModel):
      keyword: str
      city: str = ""
      platforms: list = ["boss"]
  ```
- **修复建议**:
  ```python
  platforms: list[str] = ["boss"]
  ```

---

### 4. job_service.py 条件构建逻辑重复

- **文件**: `app/services/job_service.py:113-136` 与 `app/services/job_service.py:156-183`
- **问题描述**: `query_jobs()` 和 `count_jobs()` 中有完全相同的条件构建逻辑（约 25 行），违反 DRY 原则。任何修改需要同步两个地方。
- **当前代码**: 两段几乎完全一样的 `conditions = []` + `if dto.xxx: conditions.append(...)` 逻辑。
- **修复建议**: 提取为私有方法：
  ```python
  @staticmethod
  def _build_conditions(dto: JobQueryDTO) -> list:
      conditions = []
      if dto.keyword:
          like = f"%{dto.keyword}%"
          conditions.append(or_(Job.title.like(like), ...))
      ...
      return conditions
  ```

---

### 5. job_service.py SQL 注入风险（LIKE 查询）

- **文件**: `app/services/job_service.py:116,128,158,170`
- **问题描述**: `like = f"%{dto.keyword}%"` 直接将用户输入拼接到 LIKE 模式中，如果用户输入包含 `%` 或 `_` 等 SQL LIKE 通配符，会导致意外匹配。虽然 SQLAlchemy 的 `.like()` 不会产生传统 SQL 注入（参数化查询），但 LIKE 通配符注入仍可能。
- **当前代码**:
  ```python
  like = f"%{dto.keyword}%"
  ```
- **修复建议**: 对用户输入中的 LIKE 通配符进行转义：
  ```python
  def escape_like(s: str) -> str:
      return s.replace("%", "\\%").replace("_", "\\_")

  like = f"%{escape_like(dto.keyword)}%"
  ```

---

### 6. export_service.py 裸 except 捕获

- **文件**: `app/services/export_service.py:44`
- **问题描述**: `except:` 裸捕获（无异常类型），可能掩盖真实错误。
- **当前代码**:
  ```python
  try:
      if len(str(cell.value)) > max_length:
          max_length = len(str(cell.value))
  except:
      pass
  ```
- **修复建议**: 至少捕获 `TypeError` 或 `AttributeError`：
  ```python
  except (TypeError, AttributeError):
      pass
  ```

---

### 7. scheduler.py 中 scheduled_crawl 为 async 函数但 APScheduler 可能不正确调度

- **文件**: `app/scheduler.py:15`
- **问题描述**: `scheduled_crawl()` 是 async 函数，使用 `AsyncIOScheduler` 调度。但如果 AsyncIOScheduler 的事件循环与 FastAPI/uvicorn 的不是同一个（在模块级启动 scheduler 可能导致此问题），async 函数可能在不同 loop 中运行，导致 "different event loop" 错误。
- **当前代码**:
  ```python
  scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
  scheduler.add_job(scheduled_crawl, "cron", hour=2, minute=0)
  scheduler.start()
  ```
- **修复建议**: 确保在 lifespan 中启动 scheduler（见警告1），这样 scheduler 会在 uvicorn 的同一事件循环中运行。

---

### 8. .env 中 DB_PASSWORD 为空

- **文件**: `.env:5`
- **问题描述**: `DB_PASSWORD=` 为空字符串，意味着 MySQL root 用户无密码。在生产环境这是严重安全隐患。
- **当前代码**:
  ```
  DB_PASSWORD=
  ```
- **修复建议**: 设置真实的数据库密码。

---

### 9. router `__init__.py` 只导出 auth_router，不一致

- **文件**: `app/routers/__init__.py:1`
- **问题描述**: `app/routers/__init__.py` 只导出了 `auth_router`，没有导出 `jobs_router`、`ai_router`、`crawler_router`。虽然 main.py 直接从各模块导入而非从 `__init__.py` 导入（所以不影响运行），但 `__init__.py` 不完整会导致 `from app.routers import *` 时缺少路由。
- **当前代码**:
  ```python
  from app.routers.auth import router as auth_router
  ```
- **修复建议**: 补充完整导出：
  ```python
  from app.routers.auth import router as auth_router
  from app.routers.jobs import router as jobs_router
  from app.routers.ai import router as ai_router
  from app.routers.crawler import router as crawler_router
  ```

---

### 10. batch_delete_jobs 逐个删除效率低

- **文件**: `app/services/job_service.py:97-102`
- **问题描述**: 逐个 `db.get()` + `db.delete()` 效率很低，应使用批量 DELETE 语句。
- **当前代码**:
  ```python
  async def batch_delete_jobs(db: AsyncSession, job_ids: list[int]):
      for job_id in job_ids:
          job = await db.get(Job, job_id)
          if job:
              await db.delete(job)
      await db.commit()
  ```
- **修复建议**:
  ```python
  from sqlalchemy import delete
  async def batch_delete_jobs(db: AsyncSession, job_ids: list[int]):
      stmt = delete(Job).where(Job.id.in_(job_ids))
      await db.execute(stmt)
      await db.commit()
  ```

---

### 11. intelligent_recommend 接收 `request: dict` 而非 Pydantic Model

- **文件**: `app/routers/jobs.py:156`
- **问题描述**: `intelligent_recommend` 路由接收 `request: dict`，FastAPI 不会对 dict 类型做请求体验证，用户可以提交任意结构的数据。这违反了项目中统一使用 Pydantic schema 的做法。
- **当前代码**:
  ```python
  @router.post("/recommend/intelligent")
  async def intelligent_recommend(request: dict, ...):
  ```
- **修复建议**: 定义专门的 Pydantic schema：
  ```python
  class IntelligentRecommendRequest(BaseModel):
      skills: str = ""
      education: str = ""
      experience_years: int = 0
      city: str = ""
      limit: int = 10
  ```

---

## 🟢 建议

### 1. config.py 使用 Pydantic v2 风格的 model_config

- **文件**: `app/config.py:30-32`
- **问题描述**: 当前使用 `class Config:` 内部类风格，这是 Pydantic v1 遗留写法。Pydantic v2 + pydantic-settings 推荐使用 `model_config`。
- **当前代码**:
  ```python
  class Config:
      env_file = ".env"
      env_file_encoding = "utf-8"
  ```
- **修复建议**:
  ```python
  from pydantic_settings import BaseSettings, SettingsConfigDict

  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
  ```

---

### 2. recommend 路由不需要认证

- **文件**: `app/routers/jobs.py:138-151`
- **问题描述**: `/recommend` 和 `/recommend/intelligent` 路径没有 `Depends(get_current_user)` 或 `Depends(require_admin)`，任何人都可以调用。如果推荐功能面向已登录用户个性化，应加入认证；如果是公开功能则可保留。
- **修复建议**: 根据业务需求决定是否需要认证保护。

---

### 3. boss.py 爬虫使用 httpx 而非 Playwright

- **文件**: `app/crawlers/boss.py`
- **问题描述**: TRAE_PROMPT.md 第三步要求用 Playwright 启动无头浏览器爬取 BOSS 直聘（因为 BOSS 直聘有 JS 渲染和反爬机制），但实际实现用的是 `httpx.AsyncClient` 直接请求 HTML。httpx 无法执行 JS，对于有反爬的网站大概率无法获取到岗位卡片。
- **当前代码**: 使用 `httpx.AsyncClient` 直接 GET 请求。
- **修复建议**: 如果 BOSS 直聘确实需要 JS 渲染，应改用 Playwright 实现。当前 httpx 方式可作为降级方案保留。

---

### 4. 缺少 `app/routers/user.py` 路由

- **问题描述**: 文件清单中有 `app/routers/user.py` 但实际不存在。如果需要用户管理功能（查看/编辑个人信息、管理员查看用户列表等），应创建此路由。
- **修复建议**: 根据业务需求决定是否需要 user 路由。当前 `auth.py` 有 `get/user-info` 和 `change-password`，基本覆盖了用户自身操作。

---

### 5. stat_summary 中 update_time 硬编码

- **文件**: `app/services/job_service.py:289`
- **问题描述**: `"update_time": "2026-07-06"` 是硬编码值，不会自动更新。
- **当前代码**:
  ```python
  "update_time": "2026-07-06"
  ```
- **修复建议**:
  ```python
  from datetime import datetime, timezone
  "update_time": datetime.now(timezone.utc).strftime("%Y-%m-%d")
  ```

---

### 6. Salary predictor 的 skill premium 检测逻辑可能误匹配

- **文件**: `app/recommender/salary_predictor.py:57-58`
- **问题描述**: `if skill in skills` 使用子串匹配，例如用户技能包含 "Go语言" 会匹配到 "Go"，但 "Golang" 不会匹配。同样 "人工智能" 会匹配但 "AI" 不会。
- **修复建议**: 使用更精确的匹配方式，如正则 `\bGo\b` 或完整的技能列表包含更多别名。

---

### 7. 缺少 `user.py` 路由对应的 `user_service.py`

- **问题描述**: 项目有 `app/schemas/user.py`（UserUpdateRequest, UserResponse）但没有对应的 `app/services/user_service.py` 和 `app/routers/user.py` 来使用这些 schema。
- **修复建议**: 如果需要用户管理功能（如管理员查看/编辑用户），应补齐 user_service 和 user 路由。

---

### 8. services/__init__.py 只导出 AuthService

- **文件**: `app/services/__init__.py:1`
- **问题描述**: 只导出了 `AuthService`，没有导出 `JobService`、AI 相关服务等。
- **修复建议**: 补充完整导出以方便统一引用。

---

### 9. export_service.py 导入 datetime 但文件顶部已导入

- **文件**: `app/services/export_service.py:2`
- **问题描述**: `from datetime import datetime` 在顶部已导入，第53行使用 `datetime.now()` 没有问题，但缺少 `timezone` 导入（见阻塞级问题9）。
- **修复建议**: 添加 `from datetime import datetime, timezone`。

---

### 10. 缺少 logging 配置

- **问题描述**: 全项目没有统一的 logging 配置。scheduler 吞异常（阻塞级问题4）、爬虫失败等都无处记录。仅有 `print` 或 `pass`。
- **修复建议**: 在 `app/main.py` 或单独的 `app/logging_config.py` 中配置统一日志：
  ```python
  import logging
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
  ```

---

## 专项检查结果汇总

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 1. Python 语法编译 | **通过** | 所有文件语法正确，无编译错误 |
| 2. import 解析 | **通过** | 所有 import 均指向项目内存在的模块；scheduler.py 的 `__import__()` 是代码质量问题而非解析失败 |
| 3. 异步阻塞调用 | **失败** | `app/crawlers/base.py` 中有 2 处 `time.sleep()` 在 async 上下文下使用 |
| 4. Pydantic v2 兼容性 | **失败** | 7 处 `.from_orm()` 应改为 `.model_validate()` |
| 5. CORS 配置 | **失败** | `.env` 中 CORS_ORIGINS 是 JSON 字符串而非逗号分隔列表 |
| 6. 密码哈希 | **通过** | 使用 bcrypt 哈希，正确截断到 72 字节 |
| 7. JWT_SECRET | **失败** | .env 中是明显占位符字符串 |
| 8. 路由注册 | **通过** | auth, jobs, ai, crawler 四个路由均在 main.py 注册 |
| 9. 循环导入风险 | **低风险** | scheduler.py 使用函数内延迟导入避免循环；ai_service.py 在 routers/ai.py 顶层导入可能有小风险，但实际不会循环 |

---

**审计结论**: 项目功能基本完整，5个阶段均有实现。但存在 **10个阻塞级问题** 需要优先修复，其中最关键的是 Pydantic v2 `.from_orm()` 废弃（7处）、async 中 `time.sleep()` 阻塞、JWT_SECRET 占位符、以及 import 接口参数错误。建议按阻塞级 → 警告级 → 建议级的顺序逐步修复。
