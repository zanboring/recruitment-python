import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.utils.security import hash_password

logger = logging.getLogger(__name__)


async def init_default_admin(db: AsyncSession):
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