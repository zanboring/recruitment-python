from fastapi import APIRouter, Depends, Query, UploadFile, Body, File
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



class VisionImportRequest(BaseModel):
    # 兼容两段式：先 POST preview 识别（不入库），确认后再带 validate=true 入库
    validate: bool = False


@router.post("/vision-import")
@log_action("视觉识别导入岗位")
async def vision_import(
    file: UploadFile,
    request: VisionImportRequest = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """招聘截图视觉识别导入（预览或直接入库）。

    用智谱 GLM-4V-Free 识别图片中的招聘信息并结构化：
    - 首次调用（validate=false，默认）只返回识别结果，不写库，供前端预览确认；
    - 前端确认后带 validate=true 再次提交，识别并入库（job_key 幂等去重）。
    """
    from app.services.vision_service import recognize_job_image
    from app.schemas.job import JobCreateRequest

    mime = (file.content_type or "image/jpeg")
    image_bytes = await file.read()
    if not image_bytes:
        raise AppException("图片内容为空", 400)
    if len(image_bytes) > 5 * 1024 * 1024:
        raise AppException("图片超过 5MB 限制", 400)

    try:
        job_data = await recognize_job_image(image_bytes, mime=mime)
    except RuntimeError as e:
        raise AppException(str(e), 400)
    except Exception as e:
        raise AppException(f"图片识别失败：{e}", 400)

    if not job_data.get("title"):
        raise AppException("未能在图片中识别到岗位信息，请换更清晰的截图重试", 400)

    if not request or not request.validate:
        return Result.success({"result": job_data, "saved": False})

    # 确认入库：走既有 create_job 校验与 job_key 去重口径
    try:
        create_req = JobCreateRequest(
            title=job_data["title"],
            company_name=job_data["company_name"],
            source_site="vision",
            city=job_data["city"],
            experience=job_data["experience"],
            education=job_data["education"],
            min_salary=job_data["min_salary"] or None,
            max_salary=job_data["max_salary"] or None,
            skills=job_data["skills"],
            job_desc=job_data["job_desc"],
        )
        created = await JobService.create_job(db, create_req)
        return Result.success({"result": job_data, "saved": True, "job_id": created.id})
    except ValueError as e:
        raise AppException(str(e), 400)


@router.post("/vision-batch-import")
@log_action("视觉批量导入岗位")
async def vision_batch_import(
    files: list[UploadFile] = File(...),
    request: VisionImportRequest = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """多张招聘截图批量视觉识别（预览或直接入库）。

    - 逐张识别，单张失败不影响其余（异常隔离）；
    - validate=false（默认）：只返回识别结果供预览，不写库；
    - validate=true：成功识别的逐条入库（job_key 幂等去重），失败条返回错误原因。
    """
    from app.services.vision_service import recognize_job_images_batch
    from app.schemas.job import JobCreateRequest

    if not files:
        raise AppException("未收到任何图片", 400)
    if len(files) > 20:
        raise AppException("单次最多上传 20 张图片", 400)

    # 逐张读取 + 基础校验（空/超限在服务层隔离，不整体中断）
    images = []
    for idx, f in enumerate(files):
        mime = (f.content_type or "image/jpeg")
        data = await f.read()
        if data and len(data) > 5 * 1024 * 1024:
            images.append({"index": idx, "bytes": b"", "mime": mime,
                           "error": "图片超过 5MB 限制"})
            continue
        images.append({"index": idx, "bytes": data, "mime": mime, "error": None})

    # 服务层批量识别（单张异常隔离）
    results = await recognize_job_images_batch(images)

    # 预览模式：只返回结果
    if not request or not request.validate:
        return Result.success({"results": results, "saved": False})

    # 确认入库：成功条逐条入库，返回每条的 saved/job_id
    out = []
    for r in results:
        if not r["ok"]:
            out.append({"index": r["index"], "ok": False, "error": r["error"],
                        "saved": False, "job_id": None})
            continue
        job_data = r["result"]
        try:
            create_req = JobCreateRequest(
                title=job_data["title"],
                company_name=job_data["company_name"],
                source_site="vision",
                city=job_data["city"],
                experience=job_data["experience"],
                education=job_data["education"],
                min_salary=job_data["min_salary"] or None,
                max_salary=job_data["max_salary"] or None,
                skills=job_data["skills"],
                job_desc=job_data["job_desc"],
            )
            created = await JobService.create_job(db, create_req)
            out.append({"index": r["index"], "ok": True, "saved": True,
                        "job_id": created.id, "result": job_data})
        except ValueError as e:
            out.append({"index": r["index"], "ok": False, "error": str(e),
                        "saved": False, "job_id": None})
    await db.commit()
    return Result.success({"results": out, "saved": True})


class UrlImportRequest(BaseModel):
    url: str = Field(..., description="招聘详情页链接")
    validate: bool = False


@router.post("/url-import")
@log_action("链接导入岗位")
async def url_import(
    request: UrlImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """粘贴招聘详情页链接导入（预览或直接入库）。

    用 Playwright 打开页面 → 提取正文 → GLM 结构化 →（可选）入库。
    入库时保留链接与渲染后 HTML 快照，供 job_checker 周期性核查岗位是否还在。
    """
    from app.services.url_import_service import import_job_from_url

    try:
        result = await import_job_from_url(db, request.url, validate=request.validate)
        return Result.success(result)
    except RuntimeError as e:
        raise AppException(str(e), 400)
    except Exception as e:
        raise AppException(f"链接导入失败：{e}", 400)


class SkillProfileRequest(BaseModel):
    skills: str = Field(..., min_length=1, description="我掌握的技能，逗号分隔")
    city: str = ""
    limit: int = Field(20, ge=1, le=100)


@router.post("/skill-profile")
async def skill_profile(
    request: SkillProfileRequest,
    db: AsyncSession = Depends(get_db)
):
    """输入我的技能 → 输出岗位技能覆盖率 / 缺口技能 / 市场热门技能。"""
    from app.services.skill_profile import build_skill_profile

    try:
        result = await build_skill_profile(
            db, request.skills, city=request.city, limit=request.limit
        )
        return Result.success(result)
    except ValueError as e:
        raise AppException(str(e), 400)


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
    """/api/jobs/import：按文件后缀分发导入器。

    支持 xlsx（openpyxl）与 csv（标准库 csv）两种格式；
    CSV 适合从任何数据源导出后快速灌库，字段头与 xlsx 导出保持一致：
    标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述
    """
    from app.services.export_service import import_jobs_from_csv

    file_content = await file.read()
    fname = (file.filename or "").lower()
    if fname.endswith(".csv"):
        result = await import_jobs_from_csv(db, file_content)
    else:
        result = await import_jobs_from_excel(db, file_content)
    return Result.success(result)
