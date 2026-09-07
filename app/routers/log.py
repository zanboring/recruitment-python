from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.dependencies import require_admin
from app.exceptions import AppException
from app.models.user import User
from app.models.sys_log import SysLog
from app.schemas.common import Result
from app.schemas.sys_log import SysLogResponse

router = APIRouter(prefix="/api/logs", tags=["操作日志"])


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        stmt = stmt.where(SysLog.username.like(f"%{_escape_like(username)}%", escape="\\"))
    if action:
        stmt = stmt.where(SysLog.action == action)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return Result.success({
        "list": [SysLogResponse.model_validate(log) for log in logs],
        "total": total,
        "page_num": page_num,
        "page_size": page_size
    })


@router.get("/user/{username}")
async def get_by_username(username: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    stmt = select(SysLog).where(SysLog.username == username).order_by(SysLog.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return Result.success([SysLogResponse.model_validate(log) for log in logs])


@router.delete("/clean")
async def clean_logs(days: int = 30, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    if days < 0:
        raise AppException("days 参数不能为负数", 400)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = delete(SysLog).where(SysLog.created_at < cutoff)
    result = await db.execute(stmt)
    await db.commit()
    return Result.success({"deleted": result.rowcount})
