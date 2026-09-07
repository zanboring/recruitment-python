from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import quote_plus

from app.config import settings

if settings.db_url:
    DATABASE_URL = settings.db_url
else:
    encoded_password = quote_plus(settings.db_password)
    DATABASE_URL = f"mysql+aiomysql://{settings.db_username}:{encoded_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"

engine = create_async_engine(DATABASE_URL, pool_size=20, max_overflow=5)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
