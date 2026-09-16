"""每日自动化日报服务：聚合统计 → Excel 报表 → AI 摘要 → 落库。

**RPA/自动化叙事**：一条完整自动化链路 —— 定时触发（scheduler）→ 数据聚合 →
报表产物落盘（Excel）→ 智能摘要（LLM，不可用自动降级）→ 任务状态可查询。
任一环节失败都不中断整条链：AI 不可用时退化为规则文本摘要。

设计要点：
- ``report_date`` 用**北京时区**日期（与前端展示口径一致），且唯一：
  同一天重复触发直接返回已有记录（幂等），定时任务与手动触发互不打架；
- ``stats_json`` 保存生成快照 —— 历史日报展示的是「当天看到的样子」，
  不被后续数据变更污染；
- AI 摘要复用 ``ai_service.generate_analysis_report`` 的既有降级链
  （云端 → 本地 Ollama → 规则），不重复造轮子。
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, Base
from app.models.job import Job
from app.services.job_service import JobService

logger = logging.getLogger(__name__)

# Excel 报表输出目录（相对项目根目录）
REPORT_DIR = Path("reports/daily")


def _beijing_today() -> tuple[str, datetime]:
    """返回 (北京今日日期字符串, 北京今日零点对应的 naive UTC 时间)。

    存储统一用 naive UTC（见 timeutil），但「今天」按用户所在时区（东八区，
    无夏令时，固定 +8h）计算 —— 否则每天 00:00–08:00 之间生成的日报会被
    记到「昨天」。新增岗位统计的起算点就是北京今日零点对应的 UTC。
    """
    beijing_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    beijing_date_str = beijing_now.strftime("%Y-%m-%d")
    # 北京今日零点（naive 北京时间）换算回 naive UTC
    start_of_beijing_today = datetime(
        beijing_now.year, beijing_now.month, beijing_now.day
    ) - timedelta(hours=8)
    return beijing_date_str, start_of_beijing_today


async def count_new_jobs_today(db: AsyncSession, since: datetime) -> int:
    """统计北京今日新增岗位数（created_at >= 北京今日零点）。"""
    stmt = select(func.count(Job.id)).where(Job.created_at >= since)
    result = await db.execute(stmt)
    return result.scalar() or 0


async def build_report_stats(db: AsyncSession) -> dict:
    """聚合日报所需的多维统计（复用可视化统计口径，保证数字自洽）。"""
    date_str, since = _beijing_today()
    base = await JobService.collect_analysis_stats(db)
    base["date"] = date_str
    base["new_today"] = await count_new_jobs_today(db, since)
    return base


# ---- Excel 报表 ----

def _write_sheet(ws, headers: list, rows: list) -> None:
    """写一个 sheet：表头 + 数据行，并自适应列宽。"""
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for col in ws.columns:
        max_len = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_len:
                    max_len = len(str(cell.value))
            except (TypeError, AttributeError):
                pass
        ws.column_dimensions[col[0].column_letter].width = min((max_len + 2) * 1.2, 50)


def build_excel(stats: dict) -> bytes:
    """把统计快照渲染成多 sheet 的 xlsx（内存中生成，返回字节）。"""
    summary = stats.get("summary", {})
    wb = Workbook()

    # 总览 sheet
    ws = wb.active
    ws.title = "总览"
    _write_sheet(ws, ["指标", "数值"], [
        ["统计日期", stats.get("date", "")],
        ["今日新增岗位", stats.get("new_today", 0)],
        ["累计岗位数", summary.get("total", 0)],
        ["在架岗位数", summary.get("active", 0)],
        ["平均薪资(元/月)", summary.get("avg_salary", 0)],
    ])

    # 各维度 sheet
    sheet_defs = [
        ("城市分布", ["城市", "岗位数"], stats.get("city", [])),
        ("热门技能", ["技能", "岗位数"], stats.get("skill", [])),
        ("学历要求", ["学历", "岗位数"], stats.get("education", [])),
        ("经验要求", ["经验", "岗位数"], stats.get("experience", [])),
        ("薪资分布", ["薪资区间", "岗位数"], stats.get("salary_range", [])),
    ]
    for name, headers, data in sheet_defs:
        ws = wb.create_sheet(title=name)
        def _row(item):
            return [item.get("name", ""), item.get("count", 0)]
        _write_sheet(ws, headers, [_row(item) for item in data])

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def generate_excel_file(stats: dict) -> str:
    """生成日报 Excel 并落盘，返回相对路径；失败返回空串（不影响主流程）。"""
    try:
        data = build_excel(stats)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"daily_report_{stats.get('date', '')}_{uuid.uuid4().hex[:8]}.xlsx"
        path = REPORT_DIR / filename
        path.write_bytes(data)
        logger.info("日报 Excel 已生成：%s", path)
        return str(path)
    except Exception as e:  # noqa: BLE001 - 报表落盘失败不应中断日报主流程
        logger.warning("日报 Excel 生成失败：%s", e)
        return ""


async def summarize_with_ai(stats: dict, db: AsyncSession) -> tuple[str, str]:
    """生成日报摘要，返回 (正文, 生成层: primary/local/rule)。

    复用 ``generate_analysis_report`` 的降级链；全部不可用时
    回退为纯规则的统计文本（诚实标注生成层为 rule）。
    """
    from app.services.ai_service import generate_analysis_report

    try:
        report, used_model = await generate_analysis_report(stats, db)
        if used_model in ("primary", "local") and report and report.strip():
            return report.strip(), used_model
    except Exception as e:  # noqa: BLE001
        logger.warning("日报 AI 摘要失败，回退规则摘要：%s", e)

    # 规则兜底摘要：可复现、零依赖、不编造
    summary = stats.get("summary", {})
    city = stats.get("city", [])
    top_city = city[0]["name"] + f"({city[0]['count']}个)" if city else "-"
    text = (
        f"{stats.get('date', '')} 共收录岗位 {summary.get('total', 0)} 个"
        f"（今日新增 {stats.get('new_today', 0)} 个），"
        f"在架 {summary.get('active', 0)} 个，平均薪资 {summary.get('avg_salary', 0)} 元/月。"
        f"热门城市：{top_city}。"
    )
    return text, "rule"


async def create_daily_report(db: AsyncSession, crawler) -> object:
    """生成当日日报（编排入口），返回 DailyReport 实例。

    幂等：当日已生成则直接返回已有记录。生成失败时不抛、落库为 FAILED，
    由调用方（定时任务 / 手动接口）决定是否重试。
    """
    from app.models.daily_report import DailyReport

    date_str, _ = _beijing_today()
    existing = (await db.execute(
        select(DailyReport).where(DailyReport.report_date == date_str)
    )).scalar_one_or_none()
    if existing:
        return existing

    try:
        stats = await build_report_stats(db)
        excel_path = await generate_excel_file(stats)
        summary, generated_by = await summarize_with_ai(stats, db)

        report = DailyReport(
            report_date=date_str,
            title=f"招聘市场日报 {date_str}",
            ai_summary=summary,
            generated_by=generated_by,
            stats_json=json.dumps(stats, ensure_ascii=False),
            excel_path=excel_path,
            new_jobs=stats.get("new_today", 0),
            active_jobs=stats.get("summary", {}).get("active", 0),
            total_jobs=stats.get("summary", {}).get("total", 0),
            status="GENERATED",
        )
        db.add(report)
        await db.commit()
        await db.refresh(report)
        logger.info("日报生成完成：%s（%s）", date_str, generated_by)
        return report
    except Exception as e:  # noqa: BLE001 - 落库失败要留痕而不是静默
        await db.rollback()
        logger.error("日报生成失败：%s", e, exc_info=True)
        report = DailyReport(
            report_date=date_str,
            title=f"招聘市场日报 {date_str}",
            status="FAILED",
            message=str(e),
        )
        db.add(report)
        try:
            await db.commit()
            await db.refresh(report)
        except Exception:  # noqa: BLE001 - 连 FAILED 都写不进时只能记日志
            await db.rollback()
            logger.critical("日报失败记录也无法落库：%s", e, exc_info=True)
        return report


async def get_latest_report(db: AsyncSession):
    from app.models.daily_report import DailyReport

    result = await db.execute(
        select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_report_by_id(db: AsyncSession, report_id: int):
    from app.models.daily_report import DailyReport

    return await db.get(DailyReport, report_id)


async def list_reports(db: AsyncSession, limit: int = 10, offset: int = 0) -> list:
    from app.models.daily_report import DailyReport

    result = await db.execute(
        select(DailyReport).order_by(
            DailyReport.report_date.desc()
        ).offset(offset).limit(limit)
    )
    return list(result.scalars().all())


def report_to_dict(report) -> dict:
    """DailyReport → 可 JSON 序列化的字典（stats 从 JSON 还原）。"""
    return {
        "id": report.id,
        "report_date": report.report_date,
        "title": report.title,
        "ai_summary": report.ai_summary,
        "generated_by": report.generated_by,
        "new_jobs": report.new_jobs,
        "active_jobs": report.active_jobs,
        "total_jobs": report.total_jobs,
        "status": report.status,
        "message": report.message,
        "has_excel": bool(report.excel_path),
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


def report_detail_to_dict(report) -> dict:
    """附上完整统计快照的详情视图。"""
    data = report_to_dict(report)
    try:
        data["stats"] = json.loads(report.stats_json) if report.stats_json else {}
    except (TypeError, json.JSONDecodeError):
        data["stats"] = {}
    return data