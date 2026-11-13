"""岗位数据 Excel 导入导出。

设计要点：
- **导入去重口径与爬虫、管理端新增完全一致**（app.utils.job_key）。原先导入用的
  job_key 含行号与整行内容哈希，同一份文件二次导入会生成不同的键，结果要么
  重复入库、要么撞唯一约束导致整个事务回滚、一条都进不去。
- 已存在的记录按「跳过」处理并计入 skip，而不是报错回滚。
- 导出用 model_copy 复制 DTO：直接改传入对象会污染调用方状态（原实现有此副作用）。
- 导出与导入都设了体量上限，避免一次操作把内存打满。
"""
import logging
from io import BytesIO
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.services.job_service import JobService
from app.schemas.job import JobQueryDTO
from app.utils.job_key import generate_job_key

logger = logging.getLogger(__name__)

# 单次导出行数上限：避免误点导出把几十万行一次性读进内存
EXPORT_MAX_ROWS = 50000

# 单次导入文件大小上限（字节）。xlsx 是压缩包，10MB 已可容纳数十万行
IMPORT_MAX_BYTES = 10 * 1024 * 1024

EXPORT_HEADERS = [
    "标题", "公司", "城市", "薪资(min)", "薪资(max)", "经验",
    "学历", "技能", "来源平台", "发布时间", "描述",
]


def _to_float(value) -> float:
    """把单元格值安全转成 float；空值或脏数据返回 0.0。

    不用 float(...) 裸转：单元格为空时 float(None) 会抛 TypeError，
    整行会被判为失败行。
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def export_jobs_to_excel(db: AsyncSession, query_dto: JobQueryDTO):
    """按查询条件导出岗位为 xlsx 文件流。"""
    # 复制一份再改分页：直接改传入的 DTO 会污染调用方对象状态
    export_dto = query_dto.model_copy(update={"page_num": 1, "page_size": EXPORT_MAX_ROWS})
    jobs = await JobService.query_jobs(db, export_dto)

    wb = Workbook()
    ws = wb.active
    ws.title = "岗位数据"
    ws.append(EXPORT_HEADERS)

    for job in jobs:
        publish_time = job.publish_time.strftime("%Y-%m-%d") if job.publish_time else ""
        ws.append([
            job.title,
            job.company_name,
            job.city,
            job.min_salary,
            job.max_salary,
            job.experience,
            job.education,
            job.skills,
            job.source_site,
            publish_time,
            job.job_desc or "",
        ])

    for col in ws.columns:
        max_length = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except (TypeError, AttributeError):
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[col[0].column_letter].width = min(adjusted_width, 50)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"jobs_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )



async def _import_rows(db: AsyncSession, rows, *, label: str) -> dict:
    """逐行导入岗位（CSV 与 Excel **共用这一份**逻辑）。

    为什么抽成一个函数：这两个入口原本是同构的两份循环，缺陷也就成对存在
    （A1/A2 都是"改一处漏一处"的典型）。现在只有一份可改，也就不会再分叉。

    **计数口径**（返回体的每个数字都必须能与库里的行对上）：

    ===============  ==================================================
    ``total``        数据行数（不含表头；空行也算一行）
    ``success``      真正写进库的行数 —— ``flush()`` 成功之后才 +1
    ``skip``         库里已存在，或本批前面出现过同一个 ``job_key``
    ``skipped_empty`` 标题为空的行（空行 / 只有逗号的脏行）
    ``fail``         解析失败或写库失败的行
    ===============  ==================================================

    恒等式：``success + skip + fail + skipped_empty == total``。
    有了它，调用方才可能发现「文件里有 100 行，但只有 60 行被处理过」。

    **批内去重显式用 ``seen``，不依赖 ``Session.autoflush``**：
    原实现靠「第二次 SELECT 能看到第一条还没提交的行」来判重，而这个行为完全来自
    ``autoflush=True`` 的默认值 —— 代码里没有一个字提到它，也没有一条测试覆盖它。
    ``autoflush=False`` 是常见的批量导入提性能手段，一旦有人这么改，两行都会进
    ``add``，唯一约束要等到最后的 ``commit()`` 才抛，函数抛 ``ValueError``，
    **已经算好的 success 会整体丢弃**。现在把「我见过什么」自己记下来。

    **单行写库失败只回滚这一行**：``db.begin_nested()`` 发 SAVEPOINT。
    没有它时，一次写库异常会让 SQLAlchemy 进入「必须 rollback」状态，
    之后每行的 ``execute`` 都抛 ``PendingRollbackError`` 并被计入 fail，
    最后 ``commit()`` 再抛一次 —— 三个计数全丢，接口直接 500。
    """
    success_count = 0
    skip_count = 0
    fail_count = 0
    skipped_empty = 0
    total = 0
    seen: set = set()

    for raw in rows:
        total += 1
        try:
            row = raw or {}
            title = str(row.get("标题") or "").strip()
            if not title:
                # 空标题行以前直接 continue，三个计数都不加 —— 调用方无法发现
                # 「少处理了几行」。现在单独计一档，并计入 total。
                skipped_empty += 1
                continue

            company_name = str(row.get("公司") or "").strip()
            city = str(row.get("城市") or "").strip()
            source_site = str(row.get("来源平台") or "").strip() or "import"

            # 与爬虫 / 管理端新增共用同一套指纹规则
            job_key = generate_job_key(source_site, title, company_name, city)

            # ① 批内去重：显式记账，与 flush 时机无关
            if job_key in seen:
                skip_count += 1
                continue
            seen.add(job_key)

            # ② 库内去重：幂等（同一份文件二次导入不会重复入库）
            existing = await db.execute(select(Job.id).where(Job.job_key == job_key))
            if existing.scalar_one_or_none() is not None:
                skip_count += 1
                continue

            # ③ 写库：SAVEPOINT 包住这一行，失败只回滚这一行
            async with db.begin_nested():
                db.add(Job(
                    title=title,
                    company_name=company_name,
                    city=city,
                    min_salary=_to_float(row.get("薪资(min)")),
                    max_salary=_to_float(row.get("薪资(max)")),
                    experience=str(row.get("经验") or ""),
                    education=str(row.get("学历") or ""),
                    skills=str(row.get("技能") or ""),
                    source_site=source_site,
                    job_key=job_key,
                    job_status="ACTIVE",
                    job_desc=str(row.get("描述") or ""),
                ))
                # flush 必须在**本行的 try 里**：SQL 要到 flush 才发，
                # 原来放在循环外时，这一行的写库失败会记到下一行头上，
                # 日志指认的也是错的行。
                await db.flush()
            # 只有 flush 成功后才计数 —— "加进 session"不等于"写进库"
            success_count += 1
        except Exception as e:  # noqa: BLE001 - 单行脏数据/写库失败不影响其余行
            fail_count += 1
            logger.warning("%s 第 %s 行导入失败：%s", label, total, e)

    return {
        "success": success_count,
        "skip": skip_count,
        "fail": fail_count,
        "skipped_empty": skipped_empty,
        "total": total,
    }


async def import_jobs_from_csv(db: AsyncSession, file_content: bytes) -> dict:
    """从 CSV 导入岗位，返回 {"success","skip","fail","skipped_empty","total"}（幂等）。

    表头字段与 xlsx 导出一致（中文表头）：
    标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述

    复用 import_jobs_from_excel 的逐行容错与 job_key 去重口径 ——
    同一岗位从 CSV / 爬虫 / 管理端三个入口进入都不会重复入库。
    """
    import csv
    import io

    if not file_content:
        raise ValueError("文件内容为空")
    if len(file_content) > IMPORT_MAX_BYTES:
        raise ValueError(f"文件过大，上限 {IMPORT_MAX_BYTES // 1024 // 1024}MB")

    try:
        text = file_content.decode("utf-8-sig")  # 兼容 UTF-8 BOM（Excel 导出常见）
        reader = csv.DictReader(io.StringIO(text))
        result = await _import_rows(db, reader, label="CSV")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise ValueError(f"CSV 导入失败: {e}")

    return result


async def import_jobs_from_excel(db: AsyncSession, file_content: bytes) -> dict:
    """从 xlsx 导入岗位，返回 {"success","skip","fail","skipped_empty","total"} 统计。

    - job_key 相同的记录计入 skip（幂等：同一份文件重复导入不会产生重复数据，
      也不会因唯一约束冲突而整体回滚）；
    - 逐行独立 try/except + SAVEPOINT，单行脏数据或写库失败都不影响其余行；
    - 空行（Excel 常见的尾部空行）计入 skipped_empty。
    """
    if not file_content:
        raise ValueError("文件内容为空")
    if len(file_content) > IMPORT_MAX_BYTES:
        raise ValueError(f"文件过大，上限 {IMPORT_MAX_BYTES // 1024 // 1024}MB")

    try:
        wb = load_workbook(filename=BytesIO(file_content), read_only=True)
        ws = wb.active

        rows = ws.iter_rows(values_only=True)
        raw_headers = next(rows, None)
        if raw_headers is None:
            raise ValueError("文件没有表头")
        headers = [str(h) if h is not None else "" for h in raw_headers]

        # 逐行 dict 化后再交给共用的逐行逻辑（与 CSV 完全同一条路径）
        dict_rows = (dict(zip(headers, values)) for values in rows)
        result = await _import_rows(db, dict_rows, label="Excel")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise ValueError(f"导入失败: {e}")

    return result
