from fastapi import APIRouter, Depends, Query
from pydantic import AliasChoices
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from datetime import timedelta

from app.database import get_db
from app.dependencies import require_admin
from app.exceptions import AppException
from app.models.user import User
from app.models.sys_log import SysLog
from app.schemas.common import Result
from app.schemas.sys_log import SysLogResponse
from app.utils.timeutil import utc_now

router = APIRouter(prefix="/api/logs", tags=["操作日志"])


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/list")
async def list_logs(
    page_num: int = Query(1, ge=1, validation_alias=AliasChoices("page_num", "page")),
    page_size: int = Query(20, ge=1, le=100, validation_alias=AliasChoices("page_size", "size", "pageSize")),
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
    """清理 N 天前的操作日志。

    时间基准统一为 naive UTC（app.utils.timeutil.utc_now），与写入
    DATETIME 列的时间语义一致。原先用 aware 的 datetime.now(timezone.utc)
    去比较库里的 naive 时间，在 MySQL（服务器东八区）上会产生 8 小时偏移，
    造成「该删的没删」或「删掉了不该删的」。
    """
    if days < 1:
        # days=0 语义上等于删除全部历史日志，属高危误操作，直接拒绝
        raise AppException("days 必须大于等于 1", 400)
    cutoff = utc_now() - timedelta(days=days)
    stmt = delete(SysLog).where(SysLog.created_at < cutoff)
    result = await db.execute(stmt)
    await db.commit()
    return Result.success({"deleted": result.rowcount})
