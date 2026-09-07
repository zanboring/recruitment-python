# Trae 改进指令 — Python 版补齐 Java 版功能

> 对比 Java 完整版（64 接口）发现 Python 版缺失 30 个接口 + 3 个整模块。
> 按以下 P0 → P1 顺序实现，每完成一项跑通再做下一项。

---

## P0-1：知识库模块（RAG 增强）— 最高优先级

### 新建文件

```
app/routers/knowledge.py
app/services/knowledge_service.py
app/schemas/knowledge.py
```

### 知识库 Schema（app/schemas/knowledge.py）

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class KnowledgeBaseCreate(BaseModel):
    question: str
    answer: str
    tags: str = ""
    source: str = "manual"
    quality_score: int = 0  # 0-3

class KnowledgeBaseUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    tags: Optional[str] = None
    quality_score: Optional[int] = None

class KnowledgeBaseResponse(BaseModel):
    id: int
    question: str
    answer: str
    tags: str
    source: str
    usage_count: int
    status: int
    quality_score: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
```

### 知识库路由（app/routers/knowledge.py）— 12 个接口

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.common import Result
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseResponse
from app.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])

@router.get("/list")
async def list_knowledge(page_num: int = 1, page_size: int = 20, db: AsyncSession = Depends(get_db)):
    items, total = await KnowledgeService.list_knowledge(db, page_num, page_size)
    return Result.success({"list": items, "total": total, "page_num": page_num, "page_size": page_size})

@router.get("/all")
async def get_all(db: AsyncSession = Depends(get_db)):
    items = await KnowledgeService.get_all_enabled(db)
    return Result.success(items)

@router.get("/search")
async def search(keyword: str = Query(...), db: AsyncSession = Depends(get_db)):
    items = await KnowledgeService.search(db, keyword)
    return Result.success(items)

@router.get("/{knowledge_id}")
async def get_by_id(knowledge_id: int, db: AsyncSession = Depends(get_db)):
    item = await KnowledgeService.get_by_id(db, knowledge_id)
    return Result.success(item)

@router.post("/")
async def create(request: KnowledgeBaseCreate, db: AsyncSession = Depends(get_db)):
    await KnowledgeService.create(db, request)
    return Result.success()

@router.put("/{knowledge_id}")
async def update(knowledge_id: int, request: KnowledgeBaseUpdate, db: AsyncSession = Depends(get_db)):
    await KnowledgeService.update(db, knowledge_id, request)
    return Result.success()

@router.delete("/{knowledge_id}")
async def delete(knowledge_id: int, db: AsyncSession = Depends(get_db)):
    await KnowledgeService.delete(db, knowledge_id)
    return Result.success()

@router.put("/{knowledge_id}/status")
async def toggle_status(knowledge_id: int, db: AsyncSession = Depends(get_db)):
    await KnowledgeService.toggle_status(db, knowledge_id)
    return Result.success()

@router.put("/{knowledge_id}/score")
async def set_score(knowledge_id: int, score: int = Query(..., ge=0, le=3), db: AsyncSession = Depends(get_db)):
    await KnowledgeService.set_score(db, knowledge_id, score)
    return Result.success()

@router.post("/learn")
async def learn(question: str, answer: str, tags: str = "", db: AsyncSession = Depends(get_db)):
    await KnowledgeService.learn_from_response(db, question, answer, tags)
    return Result.success()

@router.get("/preview")
async def preview(keyword: str = Query(...), db: AsyncSession = Depends(get_db)):
    context = await KnowledgeService.get_context_for_ai(db, keyword)
    return Result.success(context)

@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    result = await KnowledgeService.get_stats(db)
    return Result.success(result)
```

### 知识库服务（app/services/knowledge_service.py）

核心功能：
1. **get_context_for_ai(keyword)**: 精确匹配 → 关键词模糊搜索，将匹配的 Q&A 拼接为 AI 上下文
2. **learn_from_response(question, answer)**: 检测相似问题（精确 + LIKE），不存在则插入
3. **10 分钟缓存**: 所有启用记录缓存 10 分钟（用 functools.lru_cache 或手动 dict + timestamp）
4. **increment_usage_count**: 被用于 AI 上下文时使用次数 +1

```python
import time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, update
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate

# 关键词集合（招聘领域 60+ 关键词）
KEYWORDS = {
    "薪资", "推荐", "分析", "统计", "爬虫", "AI", "招聘", "岗位",
    "技能", "学历", "经验", "城市", "公司", "简历", "面试",
    "Spring", "Java", "Python", "Vue", "React", "MySQL", "Redis",
    "全栈", "后端", "前端", "算法", "数据", "可视化", "安全",
    "登录", "注册", "权限", "管理员", "用户", "知识库", "模型",
    "Ollama", "GLM", "降级", "流式", "SSE", "向量", "RAG"
}

_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 600  # 10 分钟


class KnowledgeService:
    @staticmethod
    async def get_all_enabled(db: AsyncSession) -> list:
        now = time.time()
        if _cache["data"] is not None and now - _cache["timestamp"] < CACHE_TTL:
            return _cache["data"]
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.status == 1
        ).order_by(KnowledgeBase.quality_score.desc(), KnowledgeBase.usage_count.desc())
        result = await db.execute(stmt)
        items = result.scalars().all()
        _cache["data"] = items
        _cache["timestamp"] = now
        return items

    @staticmethod
    async def get_context_for_ai(db: AsyncSession, keyword: str) -> str:
        """获取 AI 上下文：精确匹配 → 关键词模糊搜索"""
        # 1. 精确匹配
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.status == 1,
            KnowledgeBase.question == keyword
        ).limit(3)
        result = await db.execute(stmt)
        matches = result.scalars().all()

        # 2. 模糊搜索
        if not matches:
            stmt = select(KnowledgeBase).where(
                KnowledgeBase.status == 1,
                or_(
                    KnowledgeBase.question.like(f"%{keyword}%"),
                    KnowledgeBase.tags.like(f"%{keyword}%"),
                    KnowledgeBase.answer.like(f"%{keyword}%")
                )
            ).order_by(KnowledgeBase.quality_score.desc()).limit(5)
            result = await db.execute(stmt)
            matches = result.scalars().all()

        # 3. 增加使用次数
        for m in matches:
            await db.execute(
                update(KnowledgeBase).where(KnowledgeBase.id == m.id).values(
                    usage_count=KnowledgeBase.usage_count + 1
                )
            )
        await db.commit()

        # 4. 拼接上下文
        if not matches:
            return ""
        context = "以下是相关知识库内容，请参考回答：\n\n"
        for m in matches:
            context += f"问：{m.question}\n答：{m.answer}\n\n"
        return context

    @staticmethod
    async def learn_from_response(db: AsyncSession, question: str, answer: str, tags: str = ""):
        """从 AI 回答中学习：检测相似问题，不存在则插入"""
        # 精确匹配检测
        stmt = select(KnowledgeBase).where(KnowledgeBase.question == question)
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return  # 已存在

        # LIKE 模糊检测
        stmt = select(KnowledgeBase).where(
            or_(
                KnowledgeBase.question.like(f"%{question}%"),
                question.like(f"%{KnowledgeBase.question}%")
            )
        )
        result = await db.execute(stmt)
        if result.scalars().first():
            return  # 相似问题已存在

        # 提取关键词作为 tags
        if not tags:
            tags = ",".join([kw for kw in KEYWORDS if kw in question or kw in answer])

        kb = KnowledgeBase(
            question=question,
            answer=answer,
            tags=tags,
            source="zhipu",
            quality_score=1
        )
        db.add(kb)
        await db.commit()

    # ... 其他 CRUD 方法照常实现
```

### AI 对话集成 RAG

修改 `app/services/ai_service.py`，在调用 GLM-4 / Ollama 前先查知识库：

```python
# 在 call_glm4_stream 和 call_ollama_stream 中，构造 messages 前加入：
from app.services.knowledge_service import KnowledgeService

async def call_glm4_stream(message: str, session_id: str = "", db: AsyncSession = None):
    # ... 原有代码 ...

    # RAG 增强：查知识库
    context = ""
    if db:
        context = await KnowledgeService.get_context_for_ai(db, message)

    system_prompt = "你是一个招聘数据分析助手。"
    if context:
        system_prompt += "\n\n" + context

    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": message}]

    # ... 原有 payload 逻辑 ...

# 对话结束后自动学习
async def sse_generator(chunks_generator, session_id, user_message, db=None):
    full_response = ""
    async for chunk in chunks_generator:
        full_response += chunk
        yield f"data: {chunk}\n\n"
    if session_id:
        await update_conversation_history(session_id, user_message, full_response)
    # 自动学习到知识库
    if db and full_response and len(full_response) > 50:
        try:
            await KnowledgeService.learn_from_response(db, user_message, full_response)
        except Exception:
            pass
```

### 注册路由

在 `app/main.py` 中添加：
```python
from app.routers.knowledge import router as knowledge_router
app.include_router(knowledge_router)
```

---

## P0-2：本地规则引擎（LocalModelService）

### 新建文件

```
app/services/local_model_service.py
```

### 功能

当 GLM-4 和 Ollama 都不可用时，用规则引擎回答项目相关问题：

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.job import Job

FAQ = {
    "项目介绍": "这是一个智能招聘数据分析平台，包含数据爬取、存储、分析、AI问答全流程...",
    "技术选型": "后端：FastAPI + SQLAlchemy + MySQL；前端：Vue3 + ECharts；AI：GLM-4 + Ollama",
    "核心功能": "岗位管理、数据可视化、AI智能问答、岗位推荐、薪资预测、爬虫采集",
    "架构设计": "前后端分离，RESTful API，JWT认证，三级AI降级（Ollama→GLM-4→规则引擎）",
    "推荐算法": "基于Jaccard相似度的多因子加权推荐，技能70%+教育20%+经验10%",
    "爬虫模块": "基于Playwright爬取BOSS直聘，支持反爬策略、数据清洗、技能提取",
    "AI模块": "对接智谱GLM-4 API，支持SSE流式输出，Ollama本地模型备选，规则引擎兜底",
    "部署方案": "Docker部署，MySQL数据库，Uvicorn ASGI服务器",
}

class LocalModelService:
    @staticmethod
    async def chat(message: str, db: AsyncSession = None) -> str:
        message_lower = message.lower()

        # 1. FAQ 匹配
        for key, answer in FAQ.items():
            if key in message:
                return answer

        # 2. 数据分析能力
        if "薪资" in message and "分析" in message:
            return await LocalModelService._salary_analysis(db)
        if "岗位" in message and ("统计" in message or "分析" in message):
            return await LocalModelService._job_analysis(db)
        if "技能" in message and ("需求" in message or "排行" in message or "分析" in message):
            return await LocalModelService._skill_analysis(db)
        if "城市" in message and ("分布" in message or "分析" in message):
            return await LocalModelService._city_analysis(db)

        # 3. 默认回答
        return "我是一个招聘数据分析助手，可以回答关于招聘数据、薪资分析、岗位推荐等问题。请问您想了解什么？"

    @staticmethod
    async def _salary_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据"
        result = await db.execute(
            select(func.avg(Job.min_salary), func.avg(Job.max_salary), func.count()).where(Job.job_status == "ACTIVE")
        )
        row = result.first()
        if not row or row[2] == 0:
            return "当前没有岗位数据"
        avg_min = int(row[0]) if row[0] else 0
        avg_max = int(row[1]) if row[1] else 0
        return f"当前共有 {row[2]} 个在招岗位，平均薪资范围：{avg_min}-{avg_max} 元/月"

    # ... 其他分析方法类似
```

### 修改降级链

在 `ai_service.py` 的 `call_chat_stream` 中加入规则引擎兜底：

```python
async def call_chat_stream(message: str, session_id: str = "", db: AsyncSession = None):
    # 1. 尝试 GLM-4
    if settings.zhipuai_api_key:
        try:
            async for chunk in call_glm4_stream(message, session_id, db):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"GLM-4 failed: {e}")

    # 2. 尝试 Ollama
    if settings.ollama_enabled:
        try:
            async for chunk in call_ollama_stream(message, session_id, db):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Ollama failed: {e}")

    # 3. 规则引擎兜底
    from app.services.local_model_service import LocalModelService
    response = await LocalModelService.chat(message, db)
    yield response
```

---

## P0-3：操作日志模块

### 新建文件

```
app/routers/log.py
app/services/log_service.py
app/schemas/log.py
app/utils/log_decorator.py
```

### 日志装饰器（app/utils/log_decorator.py）

用 Python 装饰器替代 Java 的 AOP 切面：

```python
import functools
import json
import logging
from datetime import datetime
from app.models.sys_log import SysLog

logger = logging.getLogger(__name__)

def log_action(action: str):
    """操作日志装饰器，对标 Java 的 @Log 注解 + LogAspect"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 从 kwargs 中提取 db 和 user
            db = kwargs.get("db")
            user = kwargs.get("user") or kwargs.get("current_user")
            request = kwargs.get("request")

            success = True
            error_msg = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                if db:
                    try:
                        log_entry = SysLog(
                            username=user.username if user else "anonymous",
                            action=action,
                            method=f"{func.__module__}.{func.__name__}",
                            uri=getattr(request, "url", "") if request else "",
                            ip=getattr(request, "client", "").host if request else "",
                            params=json.dumps({k: str(v) for k, v in kwargs.items()}, ensure_ascii=False)[:2000],
                            success=success,
                            error_msg=error_msg,
                        )
                        db.add(log_entry)
                        await db.commit()
                    except Exception as e:
                        logger.error(f"Failed to write log: {e}")
        return wrapper
    return decorator
```

### 日志路由（app/routers/log.py）— 4 个接口

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta
from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.sys_log import SysLog
from app.schemas.common import Result

router = APIRouter(prefix="/api/logs", tags=["操作日志"])

@router.get("/list")
async def list_logs(
    page_num: int = 1,
    page_size: int = 20,
    username: str = "",
    action: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    stmt = select(SysLog).order_by(SysLog.created_at.desc())
    if username:
        stmt = stmt.where(SysLog.username.like(f"%{username}%"))
    if action:
        stmt = stmt.where(SysLog.action == action)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return Result.success({"list": logs, "total": total, "page_num": page_num, "page_size": page_size})

@router.get("/user/{username}")
async def get_by_username(username: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    stmt = select(SysLog).where(SysLog.username == username).order_by(SysLog.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    return Result.success(result.scalars().all())

@router.delete("/clean")
async def clean_logs(days: int = 30, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    cutoff = datetime.now() - timedelta(days=days)
    stmt = select(SysLog).where(SysLog.created_at < cutoff)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    for log in logs:
        await db.delete(log)
    await db.commit()
    return Result.success({"deleted": len(logs)})
```

### 在关键路由上使用装饰器

```python
from app.utils.log_decorator import log_action

@router.post("/")
@log_action("新增岗位")
async def create_job(request: JobCreateRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    ...
```

### 注册路由

在 `app/main.py` 中添加：
```python
from app.routers.log import router as log_router
app.include_router(log_router)
```

---

## P0-4：爬虫数据过滤

修改 `app/crawlers/cleaner.py`，加入过滤逻辑：

```python
SENIOR_KEYWORDS = ["高级", "资深", "主管", "经理", "总监", "架构师", "专家", "负责人", "Leader"]
INVALID_KEYWORDS = ["培训", "外包", "中介", "刷单", "诈骗", "博彩", "贷款", "保险销售", "兼职", "模特"]
FRESH_GRAD_KEYWORDS = ["应届", "实习", "校招", "0-1年", "1年以下"]

def is_senior_job(title: str, experience: str) -> bool:
    """过滤高级岗位（毕设场景不需要）"""
    for kw in SENIOR_KEYWORDS:
        if kw in title:
            return True
    if "5" in experience and "年" in experience:
        return True
    if "10" in experience and "年" in experience:
        return True
    return False

def is_invalid_job(title: str, description: str) -> bool:
    """过滤无效岗位"""
    text = title + description
    for kw in INVALID_KEYWORDS:
        if kw in text:
            return True
    return False

def is_high_salary(min_salary: float) -> bool:
    """过滤高薪岗位（>3万）"""
    return min_salary and min_salary > 30000

def is_fresh_grad_job(title: str, experience: str) -> bool:
    """判断是否应届生岗位"""
    text = title + experience
    for kw in FRESH_GRAD_KEYWORDS:
        if kw in text:
            return True
    return False


# 技能提取词库（6 大类 100+ 词）
SKILL_KEYWORDS = {
    "后端": ["Java", "Spring", "SpringBoot", "Spring Cloud", "MyBatis", "MySQL", "Redis", "Go", "PHP", ".NET", "C#", "C++", "Node.js", "Python", "Rust", "Kafka", "RabbitMQ"],
    "前端": ["Vue", "React", "Angular", "TypeScript", "JavaScript", "HTML5", "CSS3", "Webpack", "Vite", "小程序", "Uni-app", "jQuery"],
    "数据/AI": ["Python", "机器学习", "深度学习", "TensorFlow", "PyTorch", "Hadoop", "Spark", "Flink", "LLM", "NLP", "数据分析", "数据挖掘"],
    "运维": ["Linux", "Docker", "Kubernetes", "CI/CD", "Jenkins", "AWS", "阿里云", "Nginx", "Zabbix"],
    "测试": ["自动化测试", "Selenium", "JMeter", "Postman", "pytest", "Junit", "LoadRunner"],
    "其他": ["Git", "Maven", "Gradle", "Scrum", "系统设计", "微服务", "分布式", "高并发", "网络安全", "信息安全"],
}

def extract_skills(title: str, description: str) -> str:
    """从标题和描述中提取技能标签，长词优先，最多8个"""
    text = (title + " " + description).lower()
    found = []
    # 收集所有技能词，按长度降序排列（长词优先）
    all_skills = []
    for category, skills in SKILL_KEYWORDS.items():
        for skill in skills:
            all_skills.append(skill)
    all_skills.sort(key=len, reverse=True)

    for skill in all_skills:
        if skill.lower() in text and skill not in found:
            found.append(skill)
        if len(found) >= 8:
            break
    return ",".join(found)
```

### 在 crawler_service.py 的 save_job 前加入过滤

```python
from app.crawlers.cleaner import is_senior_job, is_invalid_job, is_high_salary, extract_skills

# 在 start_crawl 的 for job_data in jobs 循环中：
for job_data in jobs:
    # 过滤
    if is_senior_job(job_data["title"], job_data.get("experience", "")):
        continue
    if is_invalid_job(job_data["title"], job_data.get("description", "")):
        continue
    if is_high_salary(job_data.get("min_salary", 0)):
        continue

    # 技能提取增强
    if not job_data.get("skills"):
        job_data["skills"] = extract_skills(job_data["title"], job_data.get("description", ""))

    if await save_job(db, job_data):
        count += 1
```

---

## P0-5：岗位下架标记

修改 `app/services/crawler_service.py`，爬取后标记未出现的岗位为 OFFLINE：

```python
async def start_crawl(db: AsyncSession, keyword: str, city: str, platforms: list) -> int:
    # ... 爬取逻辑 ...

    # 爬取完成后，标记本次未出现的岗位为 OFFLINE
    seen_keys = [job_data["job_key"] for job_data in all_jobs]
    if seen_keys:
        from sqlalchemy import update
        stmt = (
            update(Job)
            .where(
                Job.job_key.notin_(seen_keys),
                Job.job_status.in_(["NEW", "ACTIVE"])
            )
            .values(job_status="OFFLINE")
        )
        await db.execute(stmt)
        await db.commit()

    # 新岗位标记为 NEW（不是 ACTIVE）
    # 修改 save_job：新岗位 job_status="NEW"，已有岗位更新为 ACTIVE
```

修改 `save_job`：

```python
async def save_job(db: AsyncSession, job_data: dict):
    existing = await db.execute(select(Job).where(Job.job_key == job_data["job_key"]))
    job = existing.scalar_one_or_none()
    if job:
        # 已有岗位：更新状态为 ACTIVE
        job.job_status = "ACTIVE"
        job.last_seen_at = datetime.now()
        return False

    # 新岗位
    job = Job(
        ...
        job_status="NEW",  # 改为 NEW
        ...
    )
    db.add(job)
    return True
```

### 在 Job 模型中添加 last_seen_at 字段

如果 `app/models/job.py` 中没有 `last_seen_at`，添加：
```python
last_seen_at = Column(DateTime, nullable=True)
```

---

## P0-6：数据初始化（启动创建管理员）

新建 `app/init_data.py`：

```python
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.utils.security import hash_password

logger = logging.getLogger(__name__)

async def init_default_admin(db: AsyncSession):
    """启动时检查并创建默认管理员"""
    stmt = select(User).where(User.username == "admin")
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        return

    admin = User(
        username="admin",
        password=hash_password("admin123"),
        role="ADMIN",
        email="admin@recruitment.com",
    )
    db.add(admin)
    await db.commit()
    logger.info("Default admin created: admin / admin123")
```

### 在 main.py 的 lifespan 中调用

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduler import start_scheduler
    start_scheduler()

    # 初始化默认管理员
    from app.database import async_session
    from app.init_data import init_default_admin
    async with async_session() as db:
        await init_default_admin(db)

    yield
    from app.scheduler import scheduler
    scheduler.shutdown()
```

---

## P1 简要指令（P0 完成后再做）

1. **模型管理模块**：新建 `app/routers/model.py`，6 个接口（status/chat/health/list/switch/reload）
2. **AI 分析接口**：在 `jobs.py` 加 `POST /api/jobs/ai-analysis`，规则引擎聚合数据 + LLM 增强报告
3. **AI 状态 + 取消**：在 `ai.py` 加 `GET /api/ai/status` 和 `POST /api/ai/cancel`
4. **薪资预测改数据库驱动**：查实际均薪而非静态系数
5. **备用数据生成**：爬虫失败时生成 20-35 条模拟数据
6. **数据清理接口**：`POST /api/data/cleanup` 删除所有岗位+爬虫任务
7. **用户列表分页+搜索**：`GET /api/user/` 加 page_num/page_size/username 参数
8. **用户启用/禁用**：`PATCH /api/user/{id}/status`

---

## 执行顺序

```
P0-6（初始化管理员，0.5h）
→ P0-3（操作日志，1天）
→ P0-4（爬虫过滤，0.5天）
→ P0-5（下架标记，0.5天）
→ P0-2（本地规则引擎，1天）
→ P0-1（知识库 RAG，2天）— 最复杂放最后

全部 P0 完成后，再做 P1。
```
