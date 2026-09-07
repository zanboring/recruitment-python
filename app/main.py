from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
