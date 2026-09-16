import sys
from contextlib import asynccontextmanager
from pathlib import Path

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
from app.routers.report import router as report_router
from app.routers.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    from app.scheduler import start_scheduler
    start_scheduler()

    # 岗位存活核查后台任务：启动即开始慢速扫库，不阻塞启动
    from app.services.job_checker import start_checker
    start_checker()

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
    app.include_router(report_router)
    app.include_router(settings_router)

    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # ================= 前端伺服（软件化：单进程跑全栈） =================
    # 优先伺服前端构建产物（含完整 Vue 页面）。资源定位顺序：
    #   1) PyInstaller 打包 -> sys._MEIPASS/frontend_dist（打进 exe 内）
    #   2) exe 同目录 frontend_dist（绿色版外置更新）
    #   3) 源码 frontend/dist（`npm run build` 产物）
    # 都没有时回退 app/static 的简单版页面，保证任何情况下根路径可用。
    dist_index = _resolve_frontend_index()
    if dist_index is not None:
        dist_dir = dist_index.parent  # frontend 产物根目录（含 assets/ 与 index.html）

        @app.get("/")
        async def root_fe():
            return FileResponse(str(dist_index))

        # Vue 构建产物的静态资源（JS/CSS/图片）
        app.mount(
            "/assets",
            StaticFiles(directory=str(dist_dir / "assets")),
            name="assets",
        )

        # SPA 路由回退（createWebHistory）：刷新 /dashboard 等前端路由时
        # 后端返回 index.html 而非 404；但 /api/*、健康检查等真实接口不拦截。
        @app.middleware("http")
        async def spa_fallback(request, call_next):
            path = request.url.path
            if (
                path.startswith("/api")
                or path.startswith("/assets")
                or path == "/docs"
                or path == "/openapi.json"
                or path == "/favicon.ico"
            ):
                return await call_next(request)
            # 非 API 的 GET 请求：交给 index.html（SPA 前端路由接管）
            if request.method == "GET":
                return FileResponse("frontend/dist/index.html")
            return await call_next(request)
    else:
        @app.get("/")
        async def root_fallback():
            return FileResponse("app/static/index.html")

        app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


def _resolve_frontend_index() -> Path:
    """解析前端构建产物的 index.html 路径（兼容源码 / PyInstaller / 绿色版）。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包：资源随 exe 释放到 _MEIPASS，或外置在 exe 同目录
        candidates = [
            Path(sys._MEIPASS) / "frontend_dist" / "index.html",  # noqa: SLF001
            Path(sys.executable).resolve().parent / "frontend_dist" / "index.html",
        ]
    else:
        candidates = [
            Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html",
            Path(__file__).resolve().parent.parent / "frontend_dist" / "index.html",
        ]
    for c in candidates:
        if c.exists():
            return c
    return None


app = create_app()
