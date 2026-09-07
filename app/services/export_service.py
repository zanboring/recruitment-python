import hashlib
from io import BytesIO
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.services.job_service import JobService
from app.schemas.job import JobQueryDTO


async def export_jobs_to_excel(db: AsyncSession, query_dto: JobQueryDTO):
    query_dto.page_size = 999999
    jobs = await JobService.query_jobs(db, query_dto)

    wb = Workbook()
    ws = wb.active
    ws.title = "岗位数据"

    headers = ["标题", "公司", "城市", "薪资(min)", "薪资(max)", "经验", "学历", "技能", "来源平台", "发布时间", "描述"]
    ws.append(headers)

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
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


async def import_jobs_from_excel(db: AsyncSession, file_content: bytes):
    success_count = 0
    fail_count = 0

    try:
        wb = load_workbook(filename=BytesIO(file_content))
        ws = wb.active

        headers = []
        for col in range(1, ws.max_column + 1):
            headers.append(ws.cell(row=1, column=col).value)

        for row in range(2, ws.max_row + 1):
            try:
                job_data = {}
                for col in range(len(headers)):
                    job_data[headers[col]] = ws.cell(row=row, column=col + 1).value

                job = Job(
                    title=str(job_data.get("标题", "")),
                    company_name=str(job_data.get("公司", "")),
                    city=str(job_data.get("城市", "")),
                    min_salary=float(job_data.get("薪资(min)", 0)),
                    max_salary=float(job_data.get("薪资(max)", 0)),
                    experience=str(job_data.get("经验", "")),
                    education=str(job_data.get("学历", "")),
                    skills=str(job_data.get("技能", "")),
                    source_site=str(job_data.get("来源平台", "import")),
                    job_key=f"import_{row}_{hashlib.sha256(str(job_data).encode()).hexdigest()}",
                    job_status="ACTIVE",
                    job_desc=str(job_data.get("描述", "")),
                )
                db.add(job)
                success_count += 1
            except Exception:
                fail_count += 1

        await db.commit()
    except Exception as e:
        await db.rollback()
        raise ValueError(f"导入失败: {str(e)}")

    return {"success": success_count, "fail": fail_count}