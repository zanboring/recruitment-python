from fastapi import APIRouter, Depends, Query, UploadFile, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.exceptions import AppException
from app.models.user import User
from app.services.job_service import JobService
from app.services.export_service import export_jobs_to_excel, import_jobs_from_excel
from app.recommender.salary_predictor import predict_salary
from app.recommender.analyzer import recommend_jobs, get_experience_years
from app.schemas.common import Result
from app.schemas.job import JobQueryDTO, JobCreateRequest, JobUpdateRequest, JobResponse
from app.utils.log_decorator import log_action


class IntelligentRecommendRequest(BaseModel):
    skills: str = ""
    education: str = ""
    experienceYears: int = 0
    city: str = ""
    limit: int = Field(10, ge=1, le=100)

router = APIRouter(prefix="/api/jobs", tags=["岗位"])


@router.post("/")
@log_action("新增岗位")
async def create_job(
    request: JobCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    try:
        result = await JobService.create_job(db, request)
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 400)


@router.put("/{job_id}")
@log_action("更新岗位")
async def update_job(
    job_id: int,
    request: JobUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    try:
        result = await JobService.update_job(db, job_id, request)
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 400)


# 注意注册顺序：静态路径 /batch 必须排在动态路径 /{job_id} 之前。
# FastAPI 按注册顺序匹配路由，若 /{job_id} 在前，请求 DELETE /api/jobs/batch
# 会命中 /{job_id} 并把 "batch" 当作 int 解析，直接返回 422 int_parsing，
# 导致批量删除接口完全不可用。
@router.delete("/batch")
@log_action("批量删除岗位")
async def batch_delete_jobs(
    job_ids: List[int] = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    await JobService.batch_delete_jobs(db, job_ids)
    return Result.success()


@router.delete("/{job_id}")
@log_action("删除岗位")
async def delete_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    try:
        await JobService.delete_job(db, job_id)
        return Result.success()
    except ValueError as e:
        raise AppException(str(e), 400)


@router.post("/page")
async def query_jobs(request: JobQueryDTO, db: AsyncSession = Depends(get_db)):
    jobs = await JobService.query_jobs(db, request)
    total = await JobService.count_jobs(db, request)
    return Result.success({
        "list": jobs,
        "total": total,
        "page_num": request.page_num,
        "page_size": request.page_size
    })


@router.get("/stat/city")
async def stat_by_city(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_city(db)
    return Result.success(result)


@router.get("/stat/company")
async def stat_by_company(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_company(db)
    return Result.success(result)


@router.get("/stat/skill")
async def stat_by_skill(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_skill(db)
    return Result.success(result)


@router.get("/stat/salary-range")
async def stat_by_salary_range(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_salary_range(db)
    return Result.success(result)


@router.get("/stat/education")
async def stat_by_education(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_education(db)
    return Result.success(result)


@router.get("/stat/experience")
async def stat_by_experience(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_experience(db)
    return Result.success(result)


@router.get("/stat/status")
async def stat_by_status(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_by_status(db)
    return Result.success(result)


@router.get("/analysis/summary")
async def analysis_summary(db: AsyncSession = Depends(get_db)):
    result = await JobService.stat_summary(db)
    return Result.success(result)


@router.get("/analysis/report")
async def analysis_report(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """LLM 增强分析报告。

    该接口会真实调用 GLM-4 / Ollama 生成报告，属于有成本的操作，
    因此要求登录（原先完全匿名可调，存在被刷爆 Token 的成本攻击面）。
    """
    from app.services.ai_service import generate_analysis_report
    stats = await JobService.collect_analysis_stats(db)

    report, used_model = await generate_analysis_report(stats, db)

    if report is None:
        # LLM 不可用，回退到规则引擎生成的纯统计摘要
        summary = stats.get("summary", {})
        fallback = (
            f"当前共有 {summary.get('total', 0)} 个岗位，其中在招 {summary.get('active', 0)} 个，"
            f"平均薪资 {summary.get('avg_salary', 0)} 元/月。"
        )
        return Result.success({
            "report": fallback,
            "usedModel": "rule_engine",
            "llmEnhanced": False,
            "stats": stats,
        })

    return Result.success({
        "report": report,
        "usedModel": used_model,
        "llmEnhanced": True,
        "stats": stats,
    })


@router.get("/predict-salary")
async def predict_salary_endpoint(
    city: str = Query(""),
    education: str = Query(""),
    experience: str = Query(""),
    skills: str = Query("")
):
    result = predict_salary(city=city, education=education, experience=experience, skills=skills)
    return Result.success(result)


@router.get("/recommend")
async def recommend_jobs_endpoint(
    skills: str = Query(""),
    education: str = Query(""),
    experience: str = Query(""),
    city: str = Query(""),
    db: AsyncSession = Depends(get_db)
):
    exp_years = get_experience_years(experience)
    result = await recommend_jobs(db, skills=skills, education=education, experience_years=exp_years, city=city)
    return Result.success(result)


@router.post("/recommend/intelligent")
async def intelligent_recommend(
    request: IntelligentRecommendRequest,
    db: AsyncSession = Depends(get_db)
):
    result = await recommend_jobs(
        db,
        skills=request.skills,
        education=request.education,
        experience_years=request.experienceYears,
        city=request.city,
        limit=request.limit
    )
    return Result.success(result)


@router.get("/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    if not await JobService.exists(db, job_id):
        raise AppException("岗位不存在", 404)
    result = await JobService.get_job(db, job_id)
    return Result.success(result)


@router.post("/export")
@log_action("导出岗位")
async def export_jobs(
    request: JobQueryDTO,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    return await export_jobs_to_excel(db, request)


@router.post("/import")
@log_action("导入岗位")
async def import_jobs(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    file_content = await file.read()
    result = await import_jobs_from_excel(db, file_content)
    return Result.success(result)
