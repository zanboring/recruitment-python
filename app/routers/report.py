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


@router.post("/{report_id}/push")
@log_action("推送日报")
async def push_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """手动把某期日报推送到配置的 Webhook。

    生成本身已会自动推送一次；这个接口用于「当时没配好，事后补推」，
    所以必须显式告知未配置，而不是返回一个含糊的成功。
    """
    report = await report_service.get_report_by_id(db, report_id)
    if not report:
        raise AppException("日报不存在", 404)
    if report.status != "GENERATED":
        raise AppException("该期日报未成功生成，没有可推送的内容", 400)

    result = await report_service.push_report(report, report_service.load_stats(report))
    if not result.attempted:
        # 未开启/未配置 URL 属于「前置条件不满足」，用 400 让调用方看清楚原因
        raise AppException(f"未执行推送：{result.detail}", 400)

    report.message = result.describe()
    await db.commit()
    await db.refresh(report)

    return Result.success({
        "success": result.success,
        "target": result.target,
        "detail": result.detail,
        "message": result.describe(),
    })


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