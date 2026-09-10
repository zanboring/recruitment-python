from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings, validate_production_settings
from app.exceptions import AppException, app_exception_handler, global_exception_handler, http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.routers.auth import router as auth_router
from app.routers.jobs import router as jobs_router
from app.routers.ai import router as ai_router
from app.routers.crawler import router as crawler_router
from app.routers.user import router as user_router
from app.routers.log import router as log_router
from app.routers.knowledge import router as knowledge_router
from app.routers.model import router as model_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    from app.scheduler import start_scheduler
    start_scheduler()

    from app.database import async_session
    from app.init_data import init_default_admin
    async with async_session() as db:
        await init_default_admin(db)

    yield
    from app.scheduler import scheduler
    scheduler.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Recruitment System",
        description="招聘系统 Python 重构版",
        version="1.0.0",
        lifespan=lifespan
    )

    # 注意顺序：add_middleware 越靠后越外层。
    # 限流先加、CORS 后加 => CORS 中间件在最外层，浏览器 OPTIONS 预检
    # 不会占用限流配额，而限流产生的 429 仍能带上跨域响应头。
    from app.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求上下文中间件最后加 => 位于最外层，任何请求（含被限流直接拒绝的）
    # 进入时都会写入 method / path / client_ip，供操作日志装饰器读取，
    # 解决「endpoint 把请求体命名为 request、拿不到 Starlette Request」的问题。
    from app.middleware.request_context import RequestContextMiddleware
    app.add_middleware(RequestContextMiddleware)

    # 前端（Vue3，原本配套 Java 版后端）按 camelCase 消费字段，而本服务按 PEP8
    # 返回 snake_case。此中间件为 JSON 响应追加 camelCase 别名（保留原字段），
    # 使前端列表页不会大面积显示 undefined。放在最外层以覆盖全部响应。
    from app.middleware.camel_case import CamelCaseCompatMiddleware
    app.add_middleware(CamelCaseCompatMiddleware)

    # 兼容层必须最先注册：它提供 Java 版前端所需的 /api/crawl/*、/api/users/*、
    # /api/data/*、/api/knowledge/preview 等别名路由。若排在原生路由之后，
    # 形如 /api/knowledge/preview 的静态路径会被原生 /api/knowledge/{id} 抢先匹配，
    # "preview" 被当 int 解析直接 422。
    from app.routers.compat import router as compat_router
    app.include_router(compat_router)

    app.include_router(auth_router)
    app.include_router(jobs_router)
    app.include_router(ai_router)
    app.include_router(crawler_router)
    app.include_router(user_router)
    app.include_router(log_router)
    app.include_router(knowledge_router)
    app.include_router(model_router)

    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    @app.get("/")
    async def root():
        return FileResponse("app/static/index.html")

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


app = create_app()
