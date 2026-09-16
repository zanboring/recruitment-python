"""pytest 公共夹具。

设计要点：
1. 全部集成测试跑在 SQLite(in-memory) 上，不依赖本地 MySQL，
   保证「克隆下来就能跑」；生产仍走 MySQL，由 DATABASE_URL 决定。
2. 使用 StaticPool —— SQLite 内存库的连接销毁即丢数据，必须让所有
   session 复用同一条连接。
3. 关闭云端模型（DeepSeek / GLM-4）与 Ollama：测试环境没有真 key，
   也避免写出依赖网络的用例；且两个云端 key 都要清，
   否则本机 .env 配了 key 的开发者会走到 primary 分支，令降级类用例失败。
4. 每个用例重置限流器与 AI 会话状态，避免用例之间互相污染。

运行方式（项目根目录）：
    python -m pytest tests/ -q
"""
import os

# 这些环境变量必须在 app.config 被导入之前就位：
# 仓库根目录有 .env 时以 .env 为准；没有 .env（CI / 他人机器）时用下面的兜底值。
os.environ.setdefault("JWT_SECRET", "pytest-only-secret-key-not-for-production")
os.environ.setdefault("JWT_EXPIRATION", "3600000")
# 必须兜底 DB_URL，不能只靠下方 TEST_DB_URL fixture：
# app/database.py 在 **模块导入时** 就 create_async_engine(DATABASE_URL)，
# 没有 .env 时 db_url 为空 → 回落到默认 MySQL → 立刻 import aiomysql，
# 该驱动在纯测试环境可不装，于是 collection 阶段直接 ModuleNotFoundError。
# 用例本身仍用 TEST_DB_URL(:memory:) 覆盖 get_db，这里只是让导入不炸。
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.config import settings

# 测试环境强制走内存库并且不联网
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


def pytest_configure(config):
    """关闭外部依赖，保证测试离线可跑。"""
    # 两个云端供应商的 key 都必须清空：只清 zhipuai 时，本机 .env 配了
    # DEEPSEEK_API_KEY 的开发者会走通 primary 分支，令「必须降级到规则引擎」
    # 一类用例失败（实测 usedModel=primary 而非 local_fallback）——
    # 测试结果随本机 .env 漂移，等于测试不 hermetic。
    # 需要验证「有 key」路径的用例（test_llm_client / test_model_service /
    # test_embedding_service）自行 patch settings，不依赖这里的默认值。
    settings.deepseek_api_key = ""
    settings.zhipuai_api_key = ""
    settings.ollama_enabled = False
    settings.embedding_enabled = False
    # 用量记录默认关闭：它有自己的独立会话（指向真实库），在单元测试里既会污染
    # 真实数据库、又会产生「表不存在」的噪音日志。需要验证统计的用例在
    # tests/test_usage.py 里显式打开并注入内存会话工厂。
    settings.ai_usage_log_enabled = False
    # 自动入库开关默认保持开启（与生产一致），需要验证它的用例自行覆盖
    settings.ai_auto_learn_enabled = True

    # 采集护栏（跨任务层）默认关闭，原因与上面几条同类：
    # - robots 检查会向目标站点发起**真实网络请求**；
    # - 域名节流会让每个用到 _crawl_platform 的用例真的睡 20 秒。
    # 需要验证它们的用例在 tests/test_throttle.py 里显式打开。
    settings.crawl_robots_check_enabled = False
    settings.crawl_domain_min_interval = 0
    settings.crawl_daily_quota_per_platform = 0   # 0 = 不限制


@pytest_asyncio.fixture
async def usage_session_factory(engine):
    """启用用量记录并把它的独立会话指向测试内存库。

    用量服务刻意不复用请求会话（避免统计失败回滚业务数据），因此测试必须
    单独提供工厂，否则记录会落到真实数据库上。
    """
    from app.services import usage_service

    maker = async_sessionmaker(engine, expire_on_commit=False)
    original_factory = usage_service._session_factory
    original_enabled = settings.ai_usage_log_enabled

    usage_service.set_session_factory(maker)
    settings.ai_usage_log_enabled = True
    try:
        yield maker
    finally:
        usage_service.set_session_factory(original_factory)
        settings.ai_usage_log_enabled = original_enabled


@pytest.fixture(autouse=True)
def _reset_in_memory_state():
    """每个用例前清空进程内的限流计数、AI 会话历史、采集节流与日配额。

    这几处都是模块级全局状态，不清理会让用例之间产生诡异的耦合
    （例如上一个用例耗掉的日配额让下一个用例直接失败）。
    """
    from app.middleware.rate_limit import default_limiter
    from app.crawlers import robots
    from app.crawlers.throttle import daily_quota, domain_gate
    from app.cache import cache as test_cache
    from app.services import ai_service

    def _reset_all():
        test_cache.clear_sync()
        default_limiter.reset()
        ai_service.reset_runtime_state()
        domain_gate.reset()
        daily_quota.reset()
        robots.clear_cache()

    _reset_all()
    yield
    _reset_all()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def app(engine):
    """构造一个全新的 FastAPI 应用并替换掉数据库依赖。

    不直接用 app.main.app：那是模块级单例，多个用例共用会互相污染
    dependency_overrides。
    """
    from app.main import create_app

    application = create_app()

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db

    # 让未捕获异常在测试里直接抛出，而不是被兜底成 500 掩盖真实原因
    async def rethrow(request, exc):
        raise exc

    application.add_exception_handler(Exception, rethrow)

    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://pytest") as c:
        yield c
