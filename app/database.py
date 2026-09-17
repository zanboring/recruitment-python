from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import quote_plus

from app.config import settings

if settings.db_url:
    DATABASE_URL = settings.db_url
else:
    encoded_password = quote_plus(settings.db_password)
    db_type = (settings.db_type or "mysql").strip().lower()
    if db_type == "postgres":
        # PostgreSQL 需要 asyncpg 驱动（见 requirements.txt）
        DATABASE_URL = (
            f"postgresql+asyncpg://{settings.db_username}:{encoded_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )
    elif db_type == "sqlite":
        db_name = (settings.db_name or 'recruitment.db').strip()
        # 绝对路径（Docker 卷 /data/... 或 Windows 盘符）原样使用，
        # 相对路径统一挂到 ./ 下，避免在非根目录启动时落到奇怪位置
        if db_name.startswith(("/", "\\\\", "./", "C:\\", "c:\\")):
            DATABASE_URL = f"sqlite+aiosqlite:///{db_name}"
        else:
            DATABASE_URL = f"sqlite+aiosqlite:///./{db_name}"
    else:
        DATABASE_URL = f"mysql+aiomysql://{settings.db_username}:{encoded_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"
def engine_kwargs_for(url: str) -> dict:
    """按方言裁剪连接池参数。

    SQLite 不支持 pool_size / max_overflow：内存库走 StaticPool、
    文件库走 SingletonThreadPool，传这两个参数会直接 TypeError：
        Invalid argument(s) 'pool_size','max_overflow' sent to create_engine()
    而测试环境与「通用版」绿色软件都以 SQLite 为默认库。
    SQLite 是本地文件库，本来也不需要 20 连接池。
    """
    return {} if url.startswith("sqlite") else {"pool_size": 20, "max_overflow": 5}


engine = create_async_engine(DATABASE_URL, **engine_kwargs_for(DATABASE_URL))
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
