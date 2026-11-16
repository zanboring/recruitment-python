import re

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, case, delete
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.schemas.job import JobQueryDTO, JobCreateRequest, JobUpdateRequest, JobResponse
from app.utils.job_key import generate_job_key

HOT_CITIES = ["北京", "上海", "深圳", "杭州", "广州"]

# ---- 可视化统计的岗位口径 ----
#
# **可见 = NEW 或 ACTIVE**（两者都代表"现在挂着"），所有统计函数必须共用这一个口径。
#
# 为什么不再是单个 "ACTIVE"（2026-11-16 实测）：
# 爬虫新建岗位写的是 ``NEW``（crawler_service.save_job），而 ``NEW → ACTIVE`` 的
# 唯一路径是**再次爬到同一岗位并命中**。555 条是一次性抓的，所以它们**永远停在 NEW**，
# 库里 ACTIVE 只有 42 条 → 九张图表合计只有 42 条，城市分布是「长沙 25 / 杭州 10 /
# 深圳 3」这种明显不成立的结果（真实分布是上海 118 / 长沙 117 / 深圳 112 / 杭州 107 /
# 北京 101）。数字本身自洽、只是口径错了 —— 这类错误最难被发现。
#
# 同源问题两轮前修过一半：``app/recommender/analyzer.py`` 与
# ``crawler_service`` 的存活核查早已是 ``in_(["NEW","ACTIVE"])``，只剩统计这一片没对齐。
# 现在把常量改成**集合**而不是单值：以后新增统计函数拿到的是一个元组，
# 想再写成 ``== VISIBLE_STATUS`` 都写不出来。
#
# 唯一的例外是 stat_by_status：它的职责就是展示各状态分布，必须不带过滤。
VISIBLE_STATUSES = ("NEW", "ACTIVE")


# ---- 经验字段的分桶（统计层归并，不改库） ----
#
# 库里的 experience 有 22 种写法（3年及以上 220 / 2年及以上 123 / 无需经验 48 /
# 1年及以上 39 / 1-3年 27 / …），stat_by_experience 直接 GROUP BY 会让图表长出
# 十几根柱子，趋势完全读不出来。
#
# 这里只改**统计口径**：库里仍按原文存（要按原文查询照样查得到），不加列、不动爬虫。
EXPERIENCE_NO_NEED = "无需经验"
EXPERIENCE_WITHIN_1 = "1年以内"
EXPERIENCE_1_3 = "1-3年"
EXPERIENCE_3_5 = "3-5年"
EXPERIENCE_5_PLUS = "5年以上"
EXPERIENCE_OTHER = "其他"

EXPERIENCE_BUCKETS = (
    EXPERIENCE_NO_NEED,
    EXPERIENCE_WITHIN_1,
    EXPERIENCE_1_3,
    EXPERIENCE_3_5,
    EXPERIENCE_5_PLUS,
    EXPERIENCE_OTHER,
)

# 「不需要经验」的几种说法。「在校 / 实习」不在任务书列的三词里，但语义相同
# （面向学生 = 不要求年限）；漏掉它们会让这类行掉进「其他」，见报告的口径说明。
_NO_EXPERIENCE_HINTS = ("无需", "不限", "应届", "在校", "实习")

# 匹配顺序很重要：开区间先于单值 —— 「1年及以上」若先被单值规则吃掉，
# 会变成 (1, 1) 落进「1年以内」，把"至少一年"读成"一年以内"，正好差一档。
_OPEN_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*年\s*(?:及以上|以上|\+)")
_CLOSED_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~至到]\s*(\d+(?:\.\d+)?)\s*年")
_SINGLE_YEAR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*年")


def parse_experience_range(raw) -> tuple[float, float | None] | None:
    """把经验文案解析成 ``(下界, 上界)``；无上界（「N年及以上」）时上界为 ``None``。

    解析不出来返回 ``None``（调用方把它归入「其他」，**不静默丢弃** —— 丢弃会让
    各桶合计对不上岗位总数，又是一个假数字）。
    """
    text = str(raw or "").strip()
    if not text:
        return None

    found = _OPEN_RANGE_RE.search(text)
    if found:                              # 「N年及以上」= 下界 N、无上界
        return float(found.group(1)), None

    found = _CLOSED_RANGE_RE.search(text)
    if found:                              # 「N-M年」= 下界 N、上界 M
        low, high = float(found.group(1)), float(found.group(2))
        if low > high:                     # 「5-3年」这种写反的脏数据：换回来而不是丢掉
            low, high = high, low
        return low, high

    found = _SINGLE_YEAR_RE.search(text)
    if found:                              # 「N年」= 下界 N、上界 N
        value = float(found.group(1))
        return value, value
    return None


def bucket_experience(raw) -> str:
    """把一条经验文案归并到一个桶（统计层用）。

    判定表（任务书 §二）+ 两处**任务书没覆盖**、由我定的边界，见下面「两处补充口径」。

    ============  ==========================================
    无需经验       含「无需 / 不限 / 应届」（另含在校、实习）
    1年以内        上界 ≤ 1
    1-3年         下界 ≤ 2 且上界 ≤ 3
    3-5年         下界 ≤ 3 且上界 ≤ 5
    5年以上        上界 > 5；或「N年及以上」且 N > 3
    其他           解析不出来（含空值）
    ============  ==========================================

    两处补充口径（任务书的表没有写，但真实数据里存在这类值）：

    1. **无上界且 N ≤ 3**（「1年及以上」39 条、「2年及以上」123 条、「3年及以上」220 条）：
       按**下界**落桶 —— N ≤ 2 → 1-3年，N = 3 → 3-5年。理由：开区间意味着
       "至少 N 年"，N 就是用人单位的要求下限；按上限判会全部挤进「5年以上」，
       按原表又会全部掉进「其他」（162+220 条），两种都不可用。
    2. **闭区间两条规则都不命中**（如「4-5年」：下界 4 > 3）：按上界归入「3-5年」。
       它显然不是"解析失败"，不该进「其他」。
    """
    text = str(raw or "")
    if any(hint in text for hint in _NO_EXPERIENCE_HINTS):
        return EXPERIENCE_NO_NEED

    parsed = parse_experience_range(text)
    if parsed is None:
        return EXPERIENCE_OTHER

    low, high = parsed
    if high is None:                       # 「N年及以上」：按要求下限落桶
        if low > 3:
            return EXPERIENCE_5_PLUS
        if low >= 3:
            return EXPERIENCE_3_5
        return EXPERIENCE_1_3

    if high <= 1:
        return EXPERIENCE_WITHIN_1
    if low <= 2 and high <= 3:
        return EXPERIENCE_1_3
    if low <= 3 and high <= 5:
        return EXPERIENCE_3_5
    if high > 5:
        return EXPERIENCE_5_PLUS
    # 兜底：上界 ≤ 5 但下界越过 3（如「4-5年」）→ 3-5年，而不是「其他」
    return EXPERIENCE_3_5


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符。

    两个必须同时满足的前提，否则转义形同虚设：
    1) 反斜杠自身要**最先**替换，否则后插入的转义符会被二次转义；
    2) 调用 .like() 时必须显式传 escape="\\\\"。不指定转义字符时，
       SQLite 把反斜杠当普通字符，"%" 仍按通配符匹配（退化成全表扫描），
       而 MySQL 的反斜杠恰好默认就是转义符 —— 同一份代码在两个库上
       表现不一致，是这个 bug 长期没被发现的原因。
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class JobService:
    @staticmethod
    async def create_job(db: AsyncSession, request: JobCreateRequest) -> JobResponse:
        job_key = JobService._generate_job_key(
            request.source_site,
            request.title,
            request.company_name or "",
            request.city or "",
        )

        stmt = select(Job).where(Job.job_key == job_key)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            raise ValueError("岗位已存在")

        job = Job(
            company_id=request.company_id,
            title=request.title,
            company_name=request.company_name,
            source_site=request.source_site,
            job_key=job_key,
            city=request.city,
            experience=request.experience,
            education=request.education,
            min_salary=request.min_salary,
            max_salary=request.max_salary,
            salary_unit=request.salary_unit,
            skills=request.skills,
            job_desc=request.job_desc,
            url=request.url
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        return JobResponse.model_validate(job)

    @staticmethod
    async def update_job(db: AsyncSession, job_id: int, request: JobUpdateRequest) -> JobResponse:
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")

        if request.company_id is not None:
            job.company_id = request.company_id
        if request.title is not None:
            job.title = request.title
        if request.company_name is not None:
            job.company_name = request.company_name
        if request.source_site is not None:
            job.source_site = request.source_site
        if request.job_status is not None:
            job.job_status = request.job_status
        if request.city is not None:
            job.city = request.city
        if request.experience is not None:
            job.experience = request.experience
        if request.education is not None:
            job.education = request.education
        if request.min_salary is not None:
            job.min_salary = request.min_salary
        if request.max_salary is not None:
            job.max_salary = request.max_salary
        if request.salary_unit is not None:
            job.salary_unit = request.salary_unit
        if request.skills is not None:
            job.skills = request.skills
        if request.job_desc is not None:
            job.job_desc = request.job_desc
        if request.url is not None:
            job.url = request.url

        await db.commit()
        await db.refresh(job)

        return JobResponse.model_validate(job)

    @staticmethod
    async def delete_job(db: AsyncSession, job_id: int):
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")

        await db.delete(job)
        await db.commit()

    @staticmethod
    async def batch_delete_jobs(db: AsyncSession, job_ids: list[int]):
        stmt = delete(Job).where(Job.id.in_(job_ids))
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def exists(db: AsyncSession, job_id: int) -> bool:
        return await db.get(Job, job_id) is not None

    @staticmethod
    async def get_job(db: AsyncSession, job_id: int) -> JobResponse:
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")
        return JobResponse.model_validate(job)

    @staticmethod
    def _build_conditions(dto: JobQueryDTO) -> list:
        conditions = []

        if dto.keyword:
            like = f"%{_escape_like(dto.keyword)}%"
            conditions.append(
                or_(
                    Job.title.like(like, escape="\\"),
                    Job.company_name.like(like, escape="\\"),
                    Job.skills.like(like, escape="\\"),
                    Job.job_desc.like(like, escape="\\")
                )
            )
        if dto.city:
            conditions.append(Job.city == dto.city)
        if dto.company_name:
            conditions.append(
                Job.company_name.like(f"%{_escape_like(dto.company_name)}%", escape="\\")
            )
        if dto.source_site:
            conditions.append(Job.source_site == dto.source_site)
        if dto.min_salary is not None:
            conditions.append(Job.max_salary >= dto.min_salary)
        if dto.max_salary is not None:
            conditions.append(Job.min_salary <= dto.max_salary)
        if dto.status:
            conditions.append(Job.job_status == dto.status)

        return conditions

    @staticmethod
    async def query_jobs(db: AsyncSession, dto: JobQueryDTO) -> list[JobResponse]:
        conditions = JobService._build_conditions(dto)

        stmt = select(Job).where(and_(*conditions))

        stmt = stmt.order_by(
            case(((Job.min_salary.isnot(None), 0)), else_=1),
            case(((Job.skills.isnot(None) & (Job.skills != ""), 0)), else_=1),
            case(((Job.city.in_(HOT_CITIES), 0)), else_=1),
            Job.created_at.desc()
        )

        stmt = stmt.offset((dto.page_num - 1) * dto.page_size).limit(dto.page_size)

        result = await db.execute(stmt)
        jobs = result.scalars().all()

        return [JobResponse.model_validate(job) for job in jobs]

    @staticmethod
    async def count_jobs(db: AsyncSession, dto: JobQueryDTO) -> int:
        conditions = JobService._build_conditions(dto)

        stmt = select(func.count(Job.id)).where(and_(*conditions))
        result = await db.execute(stmt)
        return result.scalar()

    @staticmethod
    async def stat_by_city(db: AsyncSession) -> list[dict]:
        stmt = select(Job.city, func.count(Job.id).label("count")).where(
            Job.job_status.in_(VISIBLE_STATUSES),
            Job.city.isnot(None)
        ).group_by(Job.city).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_company(db: AsyncSession) -> list[dict]:
        stmt = select(Job.company_name, func.count(Job.id).label("count")).where(
            Job.job_status.in_(VISIBLE_STATUSES),
            Job.company_name.isnot(None)
        ).group_by(Job.company_name).order_by(func.count(Job.id).desc()).limit(20)
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_skill(db: AsyncSession) -> list[dict]:
        result = await db.execute(
            select(Job.skills).where(
                Job.job_status.in_(VISIBLE_STATUSES), Job.skills.isnot(None)
            )
        )
        rows = result.scalars().all()

        skill_count = Counter()
        for skills_str in rows:
            if skills_str:
                for skill in skills_str.split(","):
                    skill = skill.strip()
                    if skill:
                        skill_count[skill] += 1

        return [{"name": k, "count": v} for k, v in skill_count.most_common(20)]

    @staticmethod
    async def stat_by_salary_range(db: AsyncSession) -> list[dict]:
        ranges = [
            (0, 5000),
            (5000, 10000),
            (10000, 15000),
            (15000, 20000),
            (20000, 30000),
            (30000, 50000),
            (50000, float("inf"))
        ]
        labels = ["0-5k", "5k-10k", "10k-15k", "15k-20k", "20k-30k", "30k-50k", "50k+"]

        result = await db.execute(
            select(Job.min_salary, Job.max_salary).where(
                Job.job_status.in_(VISIBLE_STATUSES), Job.min_salary.isnot(None)
            )
        )
        rows = result.all()

        counts = [0] * len(ranges)
        for min_sal, max_sal in rows:
            avg_sal = (min_sal + max_sal) / 2 if max_sal else min_sal
            for i, (low, high) in enumerate(ranges):
                if low <= avg_sal < high:
                    counts[i] += 1
                    break

        return [{"name": labels[i], "count": counts[i]} for i in range(len(labels))]

    @staticmethod
    async def stat_by_education(db: AsyncSession) -> list[dict]:
        stmt = select(Job.education, func.count(Job.id).label("count")).where(
            Job.job_status.in_(VISIBLE_STATUSES),
            Job.education.isnot(None)
        ).group_by(Job.education).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_experience(db: AsyncSession) -> list[dict]:
        """经验分布：把 22 种写法归并成 5+1 个桶（见 :func:`bucket_experience`）。

        分两步而不是在 SQL 里 GROUP BY 原文：归并规则是**业务口径**（要能单独测、
        以后改档位只改一处），塞进 SQL 就只能靠真库验。聚合仍在 SQL 做，
        只把「原文 → 桶」这一层搬到 Python，行数最多 22 行的量级。
        """
        stmt = select(Job.experience, func.count(Job.id).label("count")).where(
            Job.job_status.in_(VISIBLE_STATUSES),
            Job.experience.isnot(None)
        ).group_by(Job.experience)
        result = await db.execute(stmt)

        counts = Counter()
        for raw, count in result.all():
            counts[bucket_experience(raw)] += count

        # 只返回非空桶：图上不该出现一排 0 高度的柱子；按数量降序（前端口径）
        return [{"name": name, "count": n} for name, n in counts.most_common()]

    @staticmethod
    async def stat_by_status(db: AsyncSession) -> list[dict]:
        stmt = select(Job.job_status, func.count(Job.id).label("count")).group_by(
            Job.job_status
        ).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_summary(db: AsyncSession) -> dict:
        """数据概览。

        三个字段的口径必须说清楚，否则「总数 / 在架数 / 平均薪资」会各自成立
        却互相对不上：
        - ``total``      累计采集量（含已下架，代表"爬了多少"）
        - ``active``     当前**可见**量（NEW + ACTIVE，与图表口径一致）。
          键名保留 ``active`` 是为了不动前端；它现在的含义是「可见岗位数」，
          不是"状态为 ACTIVE 的行数"（那个数请用 stat_by_status）。
        - ``avg_salary`` **只统计可见岗位**：原先不带状态过滤，把已下架岗位的
          薪资也平均了进去，导致「在架 2 个岗位」与「平均薪资取自 3 个岗位」
          同时出现在一个响应里。
        """
        total = await db.execute(select(func.count(Job.id)))
        active = await db.execute(
            select(func.count(Job.id)).where(Job.job_status.in_(VISIBLE_STATUSES))
        )
        avg_sal = await db.execute(
            select(func.avg((Job.min_salary + Job.max_salary) / 2)).where(
                Job.job_status.in_(VISIBLE_STATUSES), Job.min_salary.isnot(None)
            )
        )

        # 展示用的日期按北京时间输出。
        # 原来用 datetime.now(timezone.utc) 取日期，在东八区会导致每天 00:00–08:00
        # 之间显示的"更新日期"是昨天 —— 存储统一用 naive UTC 没问题，但**展示**
        # 必须换算到用户所在时区。国内不使用夏令时，固定 +8 偏移即可，无需 tzdata。
        beijing = datetime.now(timezone(timedelta(hours=8)))
        return {
            "total": total.scalar() or 0,
            "active": active.scalar() or 0,
            "avg_salary": round(avg_sal.scalar() or 0, 2),
            "update_time": beijing.strftime("%Y-%m-%d")
        }

    @staticmethod
    def _generate_job_key(platform: str, title: str, company: str, city: str = "") -> str:
        """生成岗位指纹。

        口径与爬虫入库、Excel 导入完全一致（统一实现在 app.utils.job_key），
        否则同一岗位经不同入口会算出不同的键而重复入库。
        """
        return generate_job_key(platform, title, company, city)


    @staticmethod
    async def collect_analysis_stats(db: AsyncSession) -> dict:
        """聚合多维度统计数据，供 AI 分析报告使用。"""
        summary = await JobService.stat_summary(db)
        city = await JobService.stat_by_city(db)
        salary_range = await JobService.stat_by_salary_range(db)
        skill = await JobService.stat_by_skill(db)
        education = await JobService.stat_by_education(db)
        experience = await JobService.stat_by_experience(db)
        return {
            "summary": summary,
            "city": city[:10],
            "salary_range": salary_range,
            "skill": skill[:10],
            "education": education,
            "experience": experience,
        }
