# Java → Python 迁移指南

> 给 Trae 用的技术栈映射和项目结构方案

---

## 1. 技术栈一对一映射

| Java | Python 替代 | 说明 |
|------|------------|------|
| Spring Boot 3.2.5 | FastAPI 0.115+ | 异步优先，自动 Swagger |
| Spring Security | python-jose + passlib | JWT + BCrypt |
| MyBatis + PageHelper | SQLAlchemy 2.0 async | ORM + 分页 |
| HikariCP 连接池 | SQLAlchemy 内置池 | pool_size=20, max_overflow=5 |
| WebMagic | httpx + BeautifulSoup | 异步HTTP + HTML解析 |
| Jsoup | BeautifulSoup + lxml | HTML解析 |
| Playwright Java | Playwright Python | API基本一致 |
| EasyExcel | openpyxl | Excel读写 |
| Jackson | Pydantic v2 | 序列化/验证 |
| Lombok | Python dataclass | 自动生成 |
| @Scheduled | APScheduler | 定时任务 |
| @Async | asyncio.create_task | 异步执行 |
| springdoc-openapi | FastAPI 内置 | Swagger文档 |
| @RestControllerAdvice | FastAPI exception_handler | 全局异常 |

---

## 2. 推荐的项目目录结构

```
recruitment-python/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口, 生命周期
│   ├── config.py                  # pydantic-settings 配置
│   ├── database.py                # SQLAlchemy async engine + session
│   ├── dependencies.py            # Depends 注入 (get_db, get_current_user)
│   │
│   ├── models/                    # SQLAlchemy ORM 模型
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── job.py
│   │   ├── company.py
│   │   ├── crawl_task.py
│   │   ├── knowledge_base.py
│   │   └── sys_log.py
│   │
│   ├── schemas/                   # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── user.py
│   │   ├── job.py
│   │   ├── ai.py
│   │   ├── crawl.py
│   │   └── common.py              # Result[T] 统一响应
│   │
│   ├── routers/                   # FastAPI APIRouter
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── jobs.py
│   │   ├── ai.py
│   │   ├── model.py
│   │   ├── crawl.py
│   │   ├── data.py
│   │   ├── knowledge.py
│   │   └── logs.py
│   │
│   ├── services/                  # 业务逻辑
│   │   ├── __init__.py
│   │   ├── auth_service.py        # 登录/注册/JWT/BCrypt
│   │   ├── user_service.py
│   │   ├── job_service.py         # 岗位CRUD+统计+推荐+分析
│   │   ├── ai_service.py          # 智谱GLM-4 (同步+SSE流式)
│   │   ├── ollama_service.py      # Ollama Qwen (同步+流式)
│   │   ├── local_model_service.py # 规则引擎小模型
│   │   ├── knowledge_service.py   # 知识库管理+自学习
│   │   ├── crawl_service.py       # 爬虫编排
│   │   ├── data_service.py        # Excel导入导出+数据清洗
│   │   └── log_service.py         # 操作日志
│   │
│   ├── crawler/                   # 爬虫模块
│   │   ├── __init__.py
│   │   ├── base_parser.py         # 抽象基类
│   │   ├── boss_parser.py
│   │   ├── zhaopin_parser.py
│   │   ├── job51_parser.py
│   │   ├── liepin_parser.py
│   │   ├── parser_factory.py      # 解析器工厂
│   │   └── playwright_browser.py  # Playwright封装(单例)
│   │
│   ├── recommender/               # 推荐算法
│   │   ├── __init__.py
│   │   ├── jaccard.py             # Jaccard相似度
│   │   ├── salary_predictor.py    # 薪资预测
│   │   └── analyzer.py            # AI增强分析
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── security.py            # JWT生成/验证, BCrypt
│   │   ├── hash_util.py           # SHA256, MD5
│   │   ├── salary_parser.py       # 薪资字符串解析
│   │   └── city_cleaner.py        # 城市标准化
│   │
│   └── middleware/
│       ├── __init__.py
│       ├── jwt_auth.py            # JWT认证中间件
│       └── log_middleware.py      # 操作日志中间件
│
├── alembic/                       # 数据库迁移 (可选)
├── scripts/                       # 原有Python脚本
│   ├── add_comments_full.py
│   ├── clean_logs.py
│   └── debug_comments.py
│
├── requirements.txt
├── .env.example
└── README.md
```

---

## 3. 核心实现要点

### 3.1 配置管理 (config.py)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 数据库
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "recruitment_db"
    db_username: str = "root"
    db_password: str = ""
    
    # JWT
    jwt_secret: str
    jwt_expiration: int = 86400000  # 24小时(毫秒)
    
    # 智谱AI
    zhipuai_api_key: str = ""
    
    # Ollama
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2:7b"
    
    # 爬虫
    crawl_max_jobs: int = 200
    crawl_retry_times: int = 4
    crawl_delay_min: int = 12
    crawl_delay_max: int = 18
    crawl_timeout: int = 45
    
    # 服务
    server_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173"]
    
    class Config:
        env_file = ".env"

settings = Settings()
```

### 3.2 数据库 (database.py)

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

DATABASE_URL = f"mysql+aiomysql://{settings.db_username}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"

engine = create_async_engine(DATABASE_URL, pool_size=20, max_overflow=5)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
```

### 3.3 JWT 认证 (utils/security.py)

```python
from jose import jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta

pwd_context = CryptContext(schemes=["bcrypt"])

def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(milliseconds=settings.jwt_expiration)
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

def verify_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

def hash_password(pwd: str) -> str:
    return pwd_context.hash(pwd)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
```

### 3.4 JWT 中间件 (middleware/jwt_auth.py)

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    try:
        payload = verify_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="请先登录")
    
    user = await db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user

def require_admin(user = Depends(get_current_user)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="权限不足")
    return user
```

### 3.5 统一响应 (schemas/common.py)

```python
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional

T = TypeVar("T")

class Result(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "success"
    data: Optional[T] = None
    
    @staticmethod
    def success(data=None):
        return Result(code=0, msg="success", data=data)
    
    @staticmethod
    def failed(msg: str, code: int = 1):
        return Result(code=code, msg=msg, data=None)
```

### 3.6 操作日志中间件 (middleware/log_middleware.py)

```python
# 用装饰器实现, 替代 Spring AOP
from functools import wraps
import asyncio

def log_action(action: str, save_params: bool = True):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 记录开始
            try:
                result = await func(*args, **kwargs)
                # 异步保存成功日志
                asyncio.create_task(save_log(action, ..., success=True))
                return result
            except Exception as e:
                asyncio.create_task(save_log(action, ..., success=False, error=str(e)))
                raise
        return wrapper
    return decorator
```

### 3.7 SSE 流式输出

```python
from fastapi.responses import StreamingResponse
import httpx

@router.post("/api/ai/stream")
async def ai_stream(body: ChatRequest):
    async def generate():
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", ZHIPU_URL, json=payload, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        yield f"data: {data}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 3.8 定时任务

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("cron", hour=2, minute=0)
async def scheduled_daily_crawl():
    await crawl_service.auto_crawl()

# 在 main.py 的 lifespan 中启动
```

### 3.9 Playwright 浏览器封装

```python
from playwright.async_api import async_playwright

class PlaywrightBrowser:
    _instance = None
    
    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls.playwright = await async_playwright().start()
            cls.browser = await cls.playwright.chromium.launch(headless=True)
            cls._instance = cls()
        return cls._instance
    
    async def render(self, url: str, wait_selector: str) -> str:
        page = await self.browser.new_page()
        await page.goto(url, timeout=30000)
        await page.wait_for_selector(wait_selector, timeout=15000)
        await page.wait_for_timeout(2000)
        html = await page.content()
        await page.close()
        return html
```

### 3.10 多条件动态查询 (替代 MyBatis selectByCondition)

```python
async def query_jobs(db: AsyncSession, dto: JobQueryDTO):
    conditions = []
    
    if dto.keyword:
        like = f"%{dto.keyword}%"
        conditions.append(
            or_(Job.title.like(like), Job.company_name.like(like),
                Job.skills.like(like), Job.job_desc.like(like))
        )
    if dto.city:
        conditions.append(Job.city == dto.city)
    if dto.company_name:
        conditions.append(Job.company_name.like(f"%{dto.company_name}%"))
    if dto.source_site:
        conditions.append(Job.source_site == dto.source_site)
    if dto.min_salary is not None:
        conditions.append(Job.max_salary >= dto.min_salary)
    if dto.max_salary is not None:
        conditions.append(Job.min_salary <= dto.max_salary)
    if dto.status:
        conditions.append(Job.job_status == dto.status)
    
    stmt = select(Job).where(and_(*conditions))
    
    # 智能排序
    stmt = stmt.order_by(
        case((Job.min_salary.isnot(None), 0), else_=1),
        case((Job.skills.isnot(None) & (Job.skills != ""), 0), else_=1),
        case((Job.city.in_(HOT_CITIES), 0), else_=1),
        Job.created_at.desc()
    )
    
    # 分页
    stmt = stmt.offset((dto.page_num - 1) * dto.page_size).limit(dto.page_size)
    
    result = await db.execute(stmt)
    return result.scalars().all()
```

### 3.11 技能分布统计 (替代 MySQL SUBSTRING_INDEX)

```python
async def stat_by_skill(db: AsyncSession):
    # Python 中直接在应用层拆分, 比 SQL 嵌套 SUBSTRING_INDEX 更优雅
    result = await db.execute(select(Job.skills).where(Job.skills.isnot(None)))
    rows = result.scalars().all()
    
    skill_count = Counter()
    for skills_str in rows:
        if skills_str:
            for skill in skills_str.split(","):
                skill = skill.strip()
                if skill:
                    skill_count[skill] += 1
    
    return [{"name": k, "count": v} for k, v in skill_count.most_common(20)]
```

### 3.12 爬虫通用模式

```python
import httpx
import random
import asyncio
from bs4 import BeautifulSoup

USER_AGENTS = [...]  # 12个UA

class BaseParser:
    platform: str = ""
    list_selector: str = ""
    
    def build_search_url(self, keyword: str, city: str) -> str:
        raise NotImplementedError
    
    def parse_job_list(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(self.list_selector)
        results = []
        for card in cards:
            results.append({
                "title": self._extract(card, ".title-selector"),
                "salary": self._extract(card, ".salary-selector"),
                "company": self._extract(card, ".company-selector"),
                "url": self._extract_url(card),
            })
        return results
    
    def extract_skills(self, text: str) -> str:
        # 从6大类词库匹配
        ...
    
    def generate_job_key(self, title: str, company: str) -> str:
        raw = f"{self.platform}{title}{company}"
        return hashlib.sha256(raw.encode()).digest()[:43].hex()

    @staticmethod
    def random_delay():
        return random.uniform(12, 18)
```

---

## 4. 依赖清单 (requirements.txt)

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
sqlalchemy[asyncio]==2.0.35
aiomysql==0.2.0
pydantic==2.9.0
pydantic-settings==2.6.0
httpx==0.27.0
beautifulsoup4==4.12.3
lxml==5.3.0
playwright==1.49.0
openpyxl==3.1.5
python-multipart==0.0.9
apscheduler==3.10.4
```

---

## 5. 重构优先级建议

### 第一阶段 (核心骨架, 1-2天)
1. FastAPI 项目骨架 + 配置 + 数据库连接
2. SQLAlchemy 模型 (6个表)
3. Pydantic schemas
4. JWT认证 + 统一响应

### 第二阶段 (业务核心, 2-3天)
5. 用户认证 (登录/注册/改密)
6. 岗位CRUD + 分页查询 + 多条件筛选
7. 岗位统计 (8个统计接口)
8. 智能推荐 + 薪资预测

### 第三阶段 (AI能力, 2-3天)
9. 智谱GLM-4 API对接 (同步+SSE流式)
10. Ollama对接
11. 本地规则引擎小模型
12. 知识库管理

### 第四阶段 (爬虫+数据, 2天)
13. 爬虫框架 (4平台 + Playwright渲染)
14. Excel导入导出 + 数据清洗
15. 爬虫定时任务

### 第五阶段 (收尾, 1天)
16. 操作日志
17. CORS配置
18. 全局异常处理
19. 测试 + 文档
