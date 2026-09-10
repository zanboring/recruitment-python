"""Java 版接口兼容层 —— 让现有前端零改动对接 Python 后端。

**背景**：前端 `recruitment-system-frontend`（Vue3 + Element Plus）是按
**Java 版后端**写的，Python 重构后路径前缀与状态枚举都变了：

    前端（Java 版契约）          Python 版实际
    POST /api/crawl/task    →    POST /api/crawler/start
    GET  /api/crawl/tasks   →    GET  /api/crawler/tasks
    DELETE /api/crawl/task/{id}
    /api/users/*            →    /api/user/*
    /api/data/import|export|cleanup
    GET  /api/jobs/analysis/top-titles
    POST /api/jobs/ai-analysis
    GET  /api/logs/export
    任务状态 FINISHED        →    COMPLETED

本模块用「别名路由」把 Java 风格请求映射到既有的 service 实现，
使现有前端无需任何改动即可跑通。全部路由标注 `deprecated=True`。

**新增前端请直接对接原生路径**（/api/crawler、/api/user、/api/jobs）。

注意：本模块必须在原生路由**之前**注册 —— 否则 `/api/knowledge/preview`
会被原生的 `GET /api/knowledge/{knowledge_id}` 抢先匹配成 int 解析失败。
"""
import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from openpyxl import Workbook

from app.config import settings
from app.database import async_session, get_db
from app.dependencies import get_current_user, require_admin
from app.exceptions import AppException
from app.models.crawl_task import CrawlTask
from app.models.job import Job
from app.models.knowledge_base import KnowledgeBase
from app.models.sys_log import SysLog
from app.models.user import User
from app.schemas.common import Result
from app.schemas.job import JobQueryDTO
from app.schemas.user import UserResponse
from app.services import crawler_service, knowledge_service
from app.services.export_service import export_jobs_to_excel, import_jobs_from_excel
from app.services.job_service import JobService
from app.utils.log_decorator import log_action
from app.utils.security import create_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["兼容层（Java 版前端）"])

# 前端 TaskTable.vue 只认这四个状态，Python 版内部用 COMPLETED
_TASK_STATUS_TO_FRONTEND = {
    "PENDING": "PENDING",
    "RUNNING": "RUNNING",
    "COMPLETED": "FINISHED",
    "FAILED": "FAILED",
}


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_platform(raw) -> str:
    """把前端的 sourceSite 归一化到内部平台标识。

    实现已收敛到 ``crawler_service.normalize_platform`` —— 同一概念在两处各写
    一份时，最容易出现的缺陷就是「两个入口对同一个输入得出不同平台」。
    """
    return crawler_service.normalize_platform(raw)


def _split_cities(raw) -> List[str]:
    """前端支持多选城市，用逗号拼接传回（如 "北京,上海"）。"""
    return [c.strip() for c in str(raw or "").split(",") if c.strip()]


def _task_to_frontend(task: CrawlTask) -> dict:
    """按前端 CrawlTask 接口的结构输出（含 FINISHED 状态映射）。"""
    finished = task.status in ("COMPLETED", "FAILED")
    return {
        "id": task.id,
        "sourceSite": task.source_site,
        # 中文名由后端给出：前端不必再维护一份「标识 → 名称」映射，
        # 否则界面上会出现 boss / zhaopin 这类用户看不懂的英文标识
        "sourceSiteLabel": crawler_service.platform_label(task.source_site),
        "keyword": task.keyword,
        "city": task.city,
        "status": _TASK_STATUS_TO_FRONTEND.get(task.status, task.status),
        "jobCount": task.job_count or 0,
        # message 是失败/跳过原因的唯一载体（例如「请求的平台均未实现」），
        # 前端必须展示它，否则用户只看到一个红色「失败」标签，无从判断原因
        "message": task.message,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "finishedAt": task.updated_at.isoformat() if finished and task.updated_at else None,
    }


# 后台任务的强引用集合。
#
# asyncio 的事件循环只持有 Task 的**弱引用**（官方文档明确提示：Save a reference
# to the result of this function, to avoid a task disappearing mid-execution）。
# 直接 `asyncio.create_task(...)` 而丢弃返回值时，任务可能在执行途中被垃圾回收 ——
# 表现为爬取「莫名其妙没跑完」，日志里连异常都没有，极难排查。
# 这里显式保存引用，并在结束时自动移除，避免集合无限增长。
_background_tasks: set = set()


def _spawn_background(coro) -> None:
    """启动一个后台任务并持有其强引用，防止被 GC 中途回收。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_crawl_in_background(
    task_id: int, keyword: str, city: str, platforms: List[str]
) -> None:
    """后台执行一次爬取。

    必须使用独立 session —— 请求的 session 在响应返回时就被依赖注入关闭了。
    """
    async with async_session() as db:
        task = await db.get(CrawlTask, task_id)
        if task is None:
            logger.warning("后台爬取任务 %s 不存在，跳过", task_id)
            return
        try:
            await crawler_service.run_crawl(db, task, keyword, city, platforms)
            logger.info("后台爬取任务 %s 完成（%s / %s）", task_id, keyword, city)
        except Exception as e:  # noqa: BLE001 - run_crawl 已把任务置为 FAILED
            logger.error("后台爬取任务 %s 失败：%s", task_id, e, exc_info=True)


# ============================================================ 爬取（/api/crawl/*）


@router.get("/api/crawl/options", deprecated=True)
async def compat_crawl_options(user: User = Depends(require_admin)):
    """爬取可选项：实际支持的平台与实际收录的城市。

    前端此前把平台和城市**硬编码**在组件里（4 个平台 / 11 个城市），而后端只
    实现了 1 个平台、收录 20 个城市。后果是双向的：用户能选到必然失败的可选项，
    同时又用不到一半的可用城市。改为从后端拉取后，支持范围变化时前端零改动。

    ``platforms`` 含中文名与 ``implemented`` 标记，前端可据此把未实现的置灰
    并说明原因，而不是等任务失败后才让用户猜。
    """
    from app.crawlers.city_map import supported_cities

    return Result.success({
        "platforms": crawler_service.platform_options(),
        "cities": supported_cities(),
    })


@router.post("/api/crawl/task", deprecated=True)
@log_action("创建爬取任务")
async def compat_create_crawl_task(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """创建爬取任务并**后台异步执行**，返回首个任务 ID。

    为什么必须异步：真实爬取要翻多页、每页间隔 12~18 秒，同步执行会远超
    前端 30 秒的 axios 超时，页面只会看到"任务创建失败"。
    """
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        raise AppException("keyword 不能为空", 400)

    cities = _split_cities(payload.get("city")) or [""]
    platforms = [_normalize_platform(payload.get("sourceSite"))]

    first_task_id = None
    for city in cities:
        task = await crawler_service.create_pending_task(db, keyword, city, platforms)
        if first_task_id is None:
            first_task_id = task.id
        # 交给事件循环后台执行，接口立即返回（必须持有引用，见 _spawn_background）
        _spawn_background(
            _run_crawl_in_background(task.id, keyword, city, platforms)
        )

    return Result.success(first_task_id)


@router.get("/api/crawl/tasks", deprecated=True)
async def compat_list_crawl_tasks(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    tasks = await crawler_service.get_tasks(db)
    return Result.success([_task_to_frontend(t) for t in tasks])


@router.post("/api/crawl/task/{task_id}/start", deprecated=True)
async def compat_start_crawl_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """启动指定任务。

    Python 版在创建任务时已直接开始执行，因此这里对已开始的任务视为幂等成功；
    只有处于 PENDING 的任务才真正触发后台执行。
    """
    task = await crawler_service.get_task(db, task_id)
    if not task:
        raise AppException("任务不存在", 404)

    if task.status == "PENDING":
        platforms = [_normalize_platform(p) for p in (task.source_site or "boss").split(",")]
        _spawn_background(
            _run_crawl_in_background(task.id, task.keyword, task.city or "", platforms)
        )

    return Result.success({"id": task.id, "status": _TASK_STATUS_TO_FRONTEND.get(task.status, task.status)})


@router.delete("/api/crawl/task/{task_id}", deprecated=True)
@log_action("删除爬取任务")
async def compat_delete_crawl_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    task = await crawler_service.get_task(db, task_id)
    if not task:
        raise AppException("任务不存在", 404)
    await db.delete(task)
    await db.commit()
    return Result.success()


# ============================================================ 数据管理（/api/data/*）


@router.post("/api/data/cleanup", deprecated=True)
@log_action("清洗数据库")
async def compat_cleanup_data(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """清空岗位数据与爬取任务记录，返回删除的岗位条数（前端用它提示）。"""
    jobs_deleted = (await db.execute(delete(Job))).rowcount or 0
    await db.execute(delete(CrawlTask))
    await db.commit()
    return Result.success(jobs_deleted)


@router.get("/api/data/export", deprecated=True)
async def compat_export_data(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """导出全部岗位为 xlsx（前端以 blob 方式下载）。"""
    return await export_jobs_to_excel(db, JobQueryDTO(page_num=1, page_size=20))


@router.post("/api/data/import", deprecated=True)
@log_action("导入岗位数据")
async def compat_import_data(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    content = await file.read()
    result = await import_jobs_from_excel(db, content)
    return Result.success(result)


# ============================================================ 用户管理（/api/users/*）


@router.get("/api/users", deprecated=True)
async def compat_list_users(
    username: str = "",
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """分页查询用户。

    前端 UserManagement.vue 依赖 {list, total, pageNum, pageSize} 这个包装结构，
    与原生 GET /api/user/ 返回纯数组不同，故单独实现。
    """
    stmt = select(User).order_by(User.created_at.desc())
    if username:
        stmt = stmt.where(User.username.like(f"%{_escape_like(username)}%", escape="\\"))

    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.offset((pageNum - 1) * pageSize).limit(pageSize)
    rows = (await db.execute(stmt)).scalars().all()

    return Result.success({
        "list": [UserResponse.model_validate(u) for u in rows],
        "total": total or 0,
        "pageNum": pageNum,
        "pageSize": pageSize,
    })


class AdminUserUpdateRequest(BaseModel):
    """管理员修改用户。独立于 UserUpdateRequest —— 后者用于用户改自己资料，
    绝不允许携带 role，避免越权提权。"""

    username: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    skills: Optional[str] = None
    education: Optional[str] = None
    experienceYears: Optional[int] = None


@router.get("/api/users/{user_id}", deprecated=True)
async def compat_get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = await db.get(User, user_id)
    if not target:
        raise AppException("用户不存在", 404)
    return Result.success(UserResponse.model_validate(target))


@router.put("/api/users/{user_id}", deprecated=True)
@log_action("修改用户")
async def compat_update_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = await db.get(User, user_id)
    if not target:
        raise AppException("用户不存在", 404)

    if payload.username and payload.username != target.username:
        exists = await db.execute(select(User).where(User.username == payload.username))
        if exists.scalar_one_or_none():
            raise AppException("用户名已存在", 400)
        target.username = payload.username

    if payload.email is not None:
        target.email = payload.email
    if payload.role is not None:
        if payload.role not in ("ADMIN", "USER"):
            raise AppException("角色只能是 ADMIN 或 USER", 400)
        # 不允许把自己降级，避免把自己锁在管理后台外面
        if target.id == user.id and payload.role != "ADMIN":
            raise AppException("不能修改自己的管理员角色", 400)
        target.role = payload.role
    if payload.skills is not None:
        target.skills = payload.skills
    if payload.education is not None:
        target.education = payload.education
    if payload.experienceYears is not None:
        target.experience_years = payload.experienceYears

    await db.commit()
    await db.refresh(target)
    return Result.success(UserResponse.model_validate(target))


@router.patch("/api/users/{user_id}/status", deprecated=True)
@log_action("切换用户启用状态")
async def compat_toggle_user_status(
    user_id: int,
    enabled: bool = Query(..., description="true 启用 / false 禁用"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    if user_id == user.id and not enabled:
        raise AppException("不能禁用自己", 400)

    target = await db.get(User, user_id)
    if not target:
        raise AppException("用户不存在", 404)

    target.enabled = enabled
    await db.commit()
    return Result.success()


@router.delete("/api/users/{user_id}", deprecated=True)
@log_action("删除用户")
async def compat_delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    if user_id == user.id:
        raise AppException("不能删除自己", 400)

    target = await db.get(User, user_id)
    if not target:
        raise AppException("用户不存在", 404)

    await db.delete(target)
    await db.commit()
    return Result.success()


# ============================================================ 认证（登录页依赖）


@router.get("/api/auth/default-username", deprecated=True)
async def compat_default_username():
    """登录页表单预填用户名（仅用户名，不含密码）。"""
    return Result.success({"username": "admin"})


@router.post("/api/auth/auto-login", deprecated=True)
@log_action("自动登录")
async def compat_auto_login(db: AsyncSession = Depends(get_db)):
    """开发/演示环境的免密登录，返回管理员 Token。

    **仅在非生产环境启用**：生产（APP_ENV=production）直接 403。
    这是对应 Java 版 `/auth/auto-login` 的兼容接口，用于前端路由守卫的
    「静默登录」体验；生产环境绝不能出现无需凭证即可获得管理员 Token 的后门。
    """
    if settings.app_env.lower() in {"production", "prod"}:
        raise AppException("生产环境已禁用自动登录", 403)

    result = await db.execute(select(User).where(User.role == "ADMIN").order_by(User.id))
    admin = result.scalars().first()
    if not admin:
        raise AppException("系统尚未初始化管理员账号", 400)

    token = create_token(admin.id, admin.username, admin.role)
    return Result.success({
        "id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "email": admin.email,
        "token": token,
    })


# ============================================================ 岗位分析补充接口


@router.get("/api/jobs/analysis/top-titles", deprecated=True)
async def compat_top_titles(db: AsyncSession = Depends(get_db)):
    """热门岗位标题 TOP10（前端分析页图表使用）。"""
    stmt = (
        select(Job.title, func.count(Job.id).label("cnt"))
        .where(Job.title.isnot(None))
        .group_by(Job.title)
        .order_by(func.count(Job.id).desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).all()
    return Result.success([{"name": row[0], "count": row[1]} for row in rows])


@router.get("/api/jobs/{job_id}/detail-html", deprecated=True)
async def compat_job_detail_html(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """返回岗位详情 HTML（前端「方案A：本地详情页」使用）。"""
    job = await db.get(Job, job_id)
    if not job:
        raise AppException("岗位不存在", 404)
    return Result.success({
        "id": job.id,
        "detailHtml": job.detail_html or "",
        "url": job.url,
    })


class AIAnalysisRequest(BaseModel):
    keyword: Optional[str] = None
    city: Optional[str] = None
    sourceSite: Optional[str] = None
    minSalary: Optional[float] = None
    maxSalary: Optional[float] = None
    status: Optional[str] = None


@router.post("/api/jobs/ai-analysis", deprecated=True)
async def compat_ai_analysis(
    payload: AIAnalysisRequest = Body(default=AIAnalysisRequest()),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """结构化岗位分析（前端 JobList 的「AI 分析」弹窗）。

    返回结构与前端 AIAnalysisResult 对齐：summary / qualityJobs /
    trendAnalysis / skillDemands / salaryAnalysis / suggestions。

    数据全部来自真实统计聚合（不编造）；summary 由规则模板生成，
    因此该接口不消耗 LLM 额度、也不会因模型不可用而失败。
    """
    dto = JobQueryDTO(
        keyword=payload.keyword or None,
        city=payload.city or None,
        source_site=payload.sourceSite or None,
        min_salary=payload.minSalary,
        max_salary=payload.maxSalary,
        status=payload.status or None,
        page_num=1,
        page_size=200,
    )

    total = await JobService.count_jobs(db, dto)
    jobs = await JobService.query_jobs(db, dto)
    stats = await JobService.collect_analysis_stats(db)

    summary_stats = stats.get("summary", {})
    city_stats = stats.get("city", [])
    skill_stats = stats.get("skill", [])
    salary_dist = stats.get("salary_range", [])
    raw_jobs = jobs  # JobResponse 列表

    # ---- 优质岗位（有薪资、技能齐全的前 5 条）----
    quality_jobs = []
    for job in raw_jobs:
        if len(quality_jobs) >= 5:
            break
        salary = "面议"
        if job.min_salary and job.max_salary:
            salary = f"{int(job.min_salary)}-{int(job.max_salary)}元"
        elif job.min_salary:
            salary = f"{int(job.min_salary)}元起"
        quality_jobs.append({
            "id": job.id,
            "title": job.title,
            "companyName": job.company_name or "未知企业",
            "city": job.city or "不限",
            "salary": salary,
            "skills": job.skills or "",
            "recommendReason": f"学历要求 {job.education or '不限'}，经验 {job.experience or '不限'}",
        })

    # ---- 技能需求（带热度分级）----
    skill_demands = []
    if skill_stats:
        top_count = skill_stats[0]["count"] or 1
        for item in skill_stats[:6]:
            ratio = (item["count"] or 0) / top_count
            level = "非常热门" if ratio >= 0.8 else "热门" if ratio >= 0.4 else "一般"
            skill_demands.append({
                "skill": item["name"],
                "count": item["count"],
                "level": level,
            })

    # ---- 薪资分析 ----
    avg_salary = float(summary_stats.get("avg_salary") or 0)
    max_salary_observed = max(
        (float(j.max_salary) for j in raw_jobs if j.max_salary), default=0.0
    )
    if salary_dist:
        peak = max(salary_dist, key=lambda x: x["count"])
        salary_range_text = f"主要集中在 {peak['name']}"
    else:
        salary_range_text = "暂无数据"

    # ---- 趋势 ----
    hot_city = city_stats[0]["name"] if city_stats else "暂无"
    hot_skill = skill_stats[0]["name"] if skill_stats else "暂无"
    hot_title = raw_jobs[0].title if raw_jobs else "暂无"

    # ---- 建议 ----
    suggestions = []
    if skill_stats:
        suggestions.append(
            f"技能需求最高的是「{skill_stats[0]['name']}」，共 {skill_stats[0]['count']} 个岗位提及，建议优先补强。"
        )
    if city_stats:
        suggestions.append(
            f"岗位最集中的城市是「{city_stats[0]['name']}」（{city_stats[0]['count']} 个），可作为求职首选。"
        )
    if avg_salary:
        suggestions.append(f"当前条件岗位的平均薪资约 {avg_salary:.0f} 元/月，可据此调整期望薪资。")
    if not suggestions:
        suggestions.append("当前筛选条件下数据较少，建议放宽条件后重新分析。")

    return Result.success({
        "summary": (
            f"共分析 {total} 个岗位，在招 {summary_stats.get('active', 0)} 个，"
            f"平均薪资约 {avg_salary:.0f} 元/月。"
        ),
        "qualityJobs": quality_jobs,
        "trendAnalysis": {
            "trendText": f"热门城市 {hot_city}，热门技能 {hot_skill}。",
            "hotCity": hot_city,
            "hotSkill": hot_skill,
            "hotTitle": hot_title,
        },
        "skillDemands": skill_demands,
        "salaryAnalysis": {
            "avgSalary": f"{avg_salary:.0f} 元",
            "topSalary": f"{max_salary_observed:.0f} 元" if max_salary_observed else "暂无",
            "salaryRange": salary_range_text,
        },
        "suggestions": suggestions,
    })


# ============================================================ 知识库补充接口


@router.get("/api/knowledge/preview", deprecated=True)
async def compat_knowledge_preview(
    question: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """预览某问题会召回到的知识库上下文（前端「知识库命中预览」）。

    必须在原生 `GET /api/knowledge/{knowledge_id}` 之前注册，
    否则 "preview" 会被当作 int 解析而返回 422。
    """
    context = await knowledge_service.KnowledgeService.get_context_for_ai(db, question)
    return Result.success(context)


@router.put("/api/knowledge/{knowledge_id}/status", deprecated=True)
@log_action("设置知识条目状态")
async def compat_set_knowledge_status(
    knowledge_id: int,
    status: int = Query(..., ge=0, le=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """直接设置启用/停用。

    原生接口是 `PATCH /{id}/toggle`（翻转），前端传的是目标状态，
    语义不同，故单独提供。
    """
    item = await knowledge_service.KnowledgeService.get_by_id(db, knowledge_id)
    if not item:
        raise AppException("知识条目不存在", 404)
    item.status = status
    await db.commit()
    knowledge_service._invalidate_caches()
    return Result.success()


@router.put("/api/knowledge/{knowledge_id}/score", deprecated=True)
@log_action("设置知识条目评分")
async def compat_set_knowledge_score(
    knowledge_id: int,
    payload: dict = Body(default={}),
    score: Optional[int] = Query(None, ge=0, le=10),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """设置知识条目评分。

    前端实现是 `http.put(url, { params: { score } })` —— axios 的第二个参数是
    请求体，所以 score 实际落在 body.params.score 里；这里同时兼容 query 传参。
    """
    value = score
    if value is None:
        body_params = payload.get("params") if isinstance(payload, dict) else None
        if isinstance(body_params, dict):
            value = body_params.get("score")
    if value is None:
        raise AppException("缺少 score 参数", 400)

    try:
        item = await knowledge_service.KnowledgeService.set_score(db, knowledge_id, int(value))
    except ValueError as e:
        raise AppException(str(e), 404)
    return Result.success(item and None)


# ============================================================ 操作日志导出


@router.get("/api/logs/export", deprecated=True)
async def compat_export_logs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """导出操作日志为 xlsx（前端以 blob 方式下载）。"""
    result = await db.execute(select(SysLog).order_by(SysLog.created_at.desc()).limit(10000))
    logs = result.scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "操作日志"
    ws.append(["ID", "操作人", "操作", "方法", "请求地址", "IP", "是否成功", "错误信息", "时间"])

    for log in logs:
        ws.append([
            log.id,
            log.username,
            log.action,
            log.method,
            log.uri,
            log.ip,
            "成功" if log.success else "失败",
            log.error_msg or "",
            log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
        ])

    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(width + 4, 50)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"logs_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
