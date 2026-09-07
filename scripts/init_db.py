"""
数据库初始化脚本

用法：
    python scripts/init_db.py

功能：
    1. 按 SQLAlchemy 模型自动建表（已存在的表不会覆盖）
    2. 插入默认管理员账号（admin / admin123，已存在则跳过）

注意：建表依赖 .env 中的 MySQL 连接配置，请先 cp .env.example .env 并填写。
"""

import asyncio
import logging
import sys
from pathlib import Path

# 让脚本可以直接以 `python scripts/init_db.py` 方式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, engine, async_session  # noqa: E402
from app.models import (  # noqa: F401,E402  导入模型以注册表结构
    User,
    Job,
    Company,
    CrawlTask,
    KnowledgeBase,
    SysLog,
)
from app.init_data import init_default_admin  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("init_db")


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据表创建完成：%s", ", ".join(sorted(Base.metadata.tables.keys())))


async def main():
    try:
        await create_tables()
        async with async_session() as db:
            await init_default_admin(db)
        logger.info("初始化完成，可执行：uvicorn app.main:app --reload --port 8000")
    except Exception as e:  # noqa: BLE001
        logger.error("初始化失败：%s", e)
        logger.error("请确认 MySQL 已启动，且 .env 中 DB_HOST / DB_PORT / DB_NAME / DB_USERNAME / DB_PASSWORD 正确")
        raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
