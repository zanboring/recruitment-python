from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.exceptions import AppException
from app.models.user import User
from app.schemas.common import Result
from app.services import report_service
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/reports", tags=["自动化日报"])


@router.post("/generate")
@log_action("生成自动化日报")
async def generate_report(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """手动触发生成当日日报（幂等：当日已生成则返回已有记录）。"""
    report = await report_service.create_daily_report(db, None)
    return Result.success(report_service.report_to_dict(report))


@router.get("/latest")
async def latest_report(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取最新一期日报（含完整统计快照）。"""
    report = await report_service.get_latest_report(db)
    if not report:
        raise AppException("暂无日报，请先执行爬取后触发生成", 404)
    return Result.success(report_service.report_detail_to_dict(report))


@router.get("/list")
async def report_list(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """日报列表（按日期倒序）。"""
    reports = await report_service.list_reports(db, limit=limit, offset=offset)
    return Result.success([report_service.report_to_dict(r) for r in reports])


@router.get("/{report_id}")
async def report_detail(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单期日报详情（含统计快照）。"""
    report = await report_service.get_report_by_id(db, report_id)
    if not report:
        raise AppException("日报不存在", 404)
    return Result.success(report_service.report_detail_to_dict(report))


@router.get("/{report_id}/download")
async def download_report_excel(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """下载该期日报的 Excel 报表。"""
    report = await report_service.get_report_by_id(db, report_id)
    if not report:
        raise AppException("日报不存在", 404)
    if not report.excel_path:
        raise AppException("该期日报未生成 Excel（生成时可能失败）", 404)
    # 路径归一化后校验前缀，兼容 Windows 反斜杠与 POSIX 正斜杠
    normalized = report.excel_path.replace("\\", "/")
    if not normalized.startswith("reports/"):
        raise AppException("报表路径异常", 400)
    return FileResponse(
        report.excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"daily_report_{report.report_date}.xlsx",
    )