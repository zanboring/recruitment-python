"""爬虫任务编排：抓取 → 清洗过滤 → 入库 → 下架判定。

**这是岗位数据入库的唯一入口**：HTTP 接口（POST /api/crawler/start）与
APScheduler 定时任务都调用 start_crawl。

历史问题：定时任务曾在 scheduler.py 里自己写了一套入库逻辑，缺少清洗过滤、
下架判定、last_seen_at 刷新与重试包装，导致同一个岗位经「手动触发」和
「定时任务」两条路径得到不同结果（甚至绕过高级岗位/无效岗位过滤）。
现在统一收敛到这里，scheduler 只负责按计划调用。
"""
import logging
import re
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawlers import robots
from app.crawlers.boss import BossCrawler  # noqa: F401  保留：兼容既有引用与测试的 monkeypatch 目标
from app.crawlers.cleaner import (
    clean_job_data,
    deduplicate_jobs,
    extract_skills,
    generate_job_key,
    is_high_salary,
    is_invalid_job,
    is_senior_job,
)
from app.crawlers.registry import SUPPORTED_PLATFORMS, display_label, get_crawler, probe_url
from app.crawlers.spec_schema import REGISTERED_SPEC_SITES
from app.crawlers.throttle import await_domain_slot, daily_quota
from app.models.company import Company
from app.models.crawl_task import CrawlTask
from app.models.job import Job

logger = logging.getLogger(__name__)

# 失败状态兜底回写用的会话工厂（测试可注入，默认 app.database.async_session）。
# 与 ``log_decorator.set_session_factory`` 同一模式：测试库是内存库，
# 若兜底路径直连生产 engine，用例会把 FAILED 写进真实 data/recsys.db。
_failure_session_factory = None


def set_failure_session_factory(factory) -> None:
    """注入「任务失败状态兜底回写」的会话工厂（测试用）。"""
    global _failure_session_factory
    _failure_session_factory = factory


def _get_failure_session_factory():
    if _failure_session_factory is not None:
        return _failure_session_factory

    from app.database import async_session

    return async_session

# 岗位被判定下架前允许的「未再出现」宽限期（秒）。
OFFLINE_GRACE_SECONDS = 6 * 3600

# 已实现的抓取平台自 ``app.crawlers.registry.CRAWLER_REGISTRY`` 派生
# （此前写死 {"boss"}，新增平台时容易忘了同步这里，接口就把新平台误判为未实现）。

# ---- 平台元数据（标识 → 中文名 / 是否已实现）----
#
# 这份映射放在**后端**而不是前端，因为它是业务知识：哪个平台叫什么名字、
# 是否真的有采集实现。前端各存一份必然漂移 —— 事实上此前前端硬编码了 4 个
# 平台而后端只实现了 1 个，用户选中「智联招聘」后会得到一个必然失败的任务，
# 且失败原因界面不展示，只看到一个红色「失败」。
#
# 对外暴露见 ``platform_options()``，前端据此渲染下拉框。
PLATFORM_META = {
    "boss": {"label": "BOSS直聘"},
    "zhaopin": {"label": "智联招聘"},
    "51job": {"label": "前程无忧"},
    "liepin": {"label": "猎聘"},
}

# 平台标识别名：容错归一化用（含中文名，便于直接调接口的调用方）。
PLATFORM_ALIASES = {
    "boss": "boss",
    "zhipin": "boss",
    "boss直聘": "boss",
    "直聘": "boss",
    "zhaopin": "zhaopin",
    "智联": "zhaopin",
    "智联招聘": "zhaopin",
    "51job": "51job",
    "前程无忧": "51job",
    "liepin": "liepin",
    "猎聘": "liepin",
}

# 「已知平台」—— 与「爬虫已实现平台」（``SUPPORTED_PLATFORMS``）是**两个不同的集合**。
#
# 为什么必须分开：浏览器采集通道（油猴脚本）根本不需要服务端有对应爬虫 ——
# 数据是用户在真实浏览器里取好回传的，服务端只负责入库。若沿用「爬虫已实现」做校验，
# 想支持智联/猎聘就得先写一个完整的 Playwright 爬虫，这显然不合理。
#
# 而 ``SUPPORTED_PLATFORMS`` 仍然是「能不能启动一次爬取任务」的判据：
# 它必须与 ``get_crawler()`` 能实例化的范围一致，否则接口会把用户引向必然失败的任务。
KNOWN_PLATFORMS = frozenset(PLATFORM_META)

# ---------------------------------------------------------------- 数据来源（source_site）
#
# 允许入库的来源，三个集合的并集：
#   1. 服务端已实现爬虫 `SUPPORTED_PLATFORMS`（boss / 51job / 声明式数据源）
#   2. 浏览器采集能上报、但服务端没有爬虫的平台 `KNOWN_PLATFORMS`（zhaopin / liepin）
#   3. 演示数据 `seed`（scripts/seed_demo_data.py 专用）
#
# **为什么要有白名单**：`source_site` 是判断「这条数据是真是假」的唯一依据。
# 一旦出现空值或拼错的平台名，下游按来源做的隔离就漏了 —— 而 seed 数据混进
# 真实统计后，谁也没法再从数据本身看出来。
VALID_SOURCE_SITES = frozenset(SUPPORTED_PLATFORMS) | KNOWN_PLATFORMS | {"seed"}

# 演示数据标记：下游「真实 / 演示」的划分只认这一个值，不要另造同义词
DEMO_SOURCE_SITE = "seed"


def valid_source_sites() -> frozenset:
    """此刻允许入库的来源集合。

    **每次调用现算，不用导入时的快照**：`SUPPORTED_PLATFORMS` 是启动时由
    `specs/` 目录算出的，而声明式数据源可以在运行期加载（`load_spec_file`）。
    用快照会让「加一个 yaml 就接入新源」在入库环节被拒 —— 抓到了却存不进去，
    排查时看到的是「采集成功 0 条」，根因却在来源校验。
    """
    return VALID_SOURCE_SITES | frozenset(REGISTERED_SPEC_SITES)


def normalize_source_site(raw) -> str:
    """归一化来源标识；空值或未知值**抛 ValueError**（拒绝入库）。

    与 `normalize_platform()` 的关键区别（不要混用）：
    `normalize_platform("")` 会**回退到 boss** —— 那是「用户没选平台」时给 UI 用的兜底。
    入库场景绝不能这样：把未知来源写成 boss，等于把「来源不可信」这条信息永久抹掉，
    数据之后再也分不出真假。所以这里宁可拒绝，也不猜。
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("source_site 为空，无法判断数据来源")

    key = PLATFORM_ALIASES.get(text) or PLATFORM_ALIASES.get(text.lower()) or text.lower()
    allowed = valid_source_sites()
    if key not in allowed:
        raise ValueError(
            f"未知 source_site={text!r}（允许：{', '.join(sorted(allowed))}）"
        )
    return key


async def count_jobs_by_source(db: AsyncSession) -> dict:
    """按来源统计岗位数（只读）。

    返回::

        {"by_source": {"seed": 196, "boss": 1}, "total": 197,
         "demo": 196, "real": 1, "unknown": 0}

    **为什么需要它**：项目此前的状态是「196 条岗位全部来自 seed 脚本」，
    但没有任何口径能回答「这批里有多少是真的」—— 图表把演示数据与真实采集
    混在一起，面试被问「这些数据哪来的」答不上来。有了这个统计，
    「真实 / 演示」的边界就是一次查询，而不是靠人记得。
    """
    rows = (
        await db.execute(select(Job.source_site, func.count()).group_by(Job.source_site))
    ).all()
    by_source = {(source or ""): int(count) for source, count in rows}
    total = sum(by_source.values())
    demo = by_source.get(DEMO_SOURCE_SITE, 0)
    unknown = by_source.get("", 0)
    return {
        "by_source": by_source,
        "total": total,
        "demo": demo,
        # 「真实」= 非 seed 且来源非空。非 0 的 unknown 说明有绕过校验的历史写入。
        "real": total - demo - unknown,
        "unknown": unknown,
    }


def normalize_platform(raw) -> str:
    """把外部传入的平台标识归一化到内部标识。

    未知值**原样返回**，交给请求前置校验拦截并给出明确原因；
    绝不回退到某个已实现平台 —— 回退会让「选智联招聘」静默变成「爬 BOSS」，
    数据被记在错误的平台名下，比直接报错难排查得多（与城市编码同一类问题）。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return "boss"
    return PLATFORM_ALIASES.get(value, value)


def normalize_platforms(platforms) -> list:
    """批量归一化平台标识并去重（保持输入顺序）。

    归一化必须发生在**服务层入口**，而不是只挂在 HTTP 兼容层上。此前它只写在
    compat 层的 ``_normalize_platform`` 里，于是同一个输入会得到两种结果：
    走兼容接口传「BOSS直聘」能正确识别，走原生接口或直接调 service 却会被判成
    「平台未实现」而任务失败 —— 同一规则两处生效范围不同，是这类缺陷的典型成因。
    """
    result: list = []
    for raw in platforms or []:
        # 必须先排除 None：str(None) 会得到 "None"，进一步 lower 成 "none"，
        # 一个「空的平台项」就变成了一个叫 "none" 的平台，最后以
        # 「平台未实现：none」的莫名其妙理由失败。
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        key = normalize_platform(value)
        if key not in result:
            result.append(key)
    return result


def platform_label(raw) -> str:
    """平台标识 → 可展示的中文名（未知标识原样返回）。

    支持逗号分隔的多平台（任务可能同时指定多个平台）。中文名由后端提供，
    前端就不必再维护一份「标识 → 名称」映射 —— 那种映射一旦漂移，
    界面上就会出现用户看不懂的英文标识。
    """
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return str(raw or "")
    labels = []
    for part in parts:
        key = normalize_platform(part)
        meta = PLATFORM_META.get(key)
        labels.append(meta["label"] if meta else display_label(key))
    return "、".join(labels)


def platform_options() -> list:
    """平台选项列表（供接口暴露，含中文名与是否已实现）。

    已实现的排在前面 —— 前端可据此把未实现的置灰，用户就不会选到
    注定失败的可选项。
    """
    keys = sorted(
        set(PLATFORM_META) | SUPPORTED_PLATFORMS,
        key=lambda k: (k not in SUPPORTED_PLATFORMS, k),
    )
    return [
        {
            "value": key,
            "label": PLATFORM_META.get(key, {}).get("label") or display_label(key),
            "implemented": key in SUPPORTED_PLATFORMS,
        }
        for key in keys
    ]


def _utc_now():
    """naive UTC 当前时间，统一入口见 app.utils.timeutil.utc_now。"""
    from app.utils.timeutil import utc_now
    return utc_now()


def normalize_job(job_data: dict) -> dict:
    """入库前补齐薪资字段（``min_salary`` / ``max_salary``）。

    **为什么必须有这一步**：各爬虫的输出契约并不一致 —— ``job51`` 在 ``crawl()``
    内部就调用了 ``clean_job_data``（自带 min/max），而 ``boss`` 只产出原始
    ``salary`` 字符串（"15-25K"）。``save_job`` 按 ``job_data["min_salary"]`` 取
    值，于是 BOSS 的**每一条新岗位**都抛 ``KeyError: 'min_salary'``，整个任务被
    外层标成 FAILED；而库里已存在的岗位走 update 分支不碰该键，所以「抓到的全是
    老岗位」时反而看起来成功，故障被完美掩盖。

    归一化放在服务层（入库的唯一入口）而不是各爬虫里：新增爬虫不必记得调用，
    契约由这里统一保证。已有 min/max 的数据原样返回，避免二次
    ``clean_job_data`` 把 job51 算好的值按空 salary 覆盖成 0。
    """
    if job_data.get("min_salary") is not None and job_data.get("max_salary") is not None:
        return job_data
    # clean_job_data 内部对每个字段做 .strip()，None 会抛 AttributeError，先兜一层
    safe = {k: ("" if v is None else v) for k, v in job_data.items()}
    return clean_job_data(safe)


# ---------------------------------------------------------------------------
# 公司实体：`job.company_id` 的解析规则
#
# **为什么之前公司表是空的**：`Company` 模型一直存在，但全仓库没有任何一处代码
# 创建过它 —— `save_job`（本文件）只写 `company_name` 文本，`scripts/seed_demo_data.py`
# 同样只写文本；而 `company_id` 唯一的赋值入口是 `job_service.create_job` 里
# `request.company_id`（管理端手填），实际没人填。于是 `company` 表 0 行、
# `job.company_id` 全为 NULL，公司维度分析只能退化到按 `company_name` 字符串聚合。
# ---------------------------------------------------------------------------

# 与 `Company.name` 的列宽一致（String(200)）
COMPANY_NAME_MAX = 200


def normalize_company_name(raw) -> str:
    """公司名归一化：全角空格转半角、压缩连续空白、去掉首尾、按列宽截断。

    为什么必须归一化：同一个公司名从不同站点/浏览器上报进来时常带首尾空格
    （"字节跳动 " / "字节跳动"），不归一化就会在 `company` 表里分裂成两条，
    公司维度统计随之失真 —— 而且这种脏数据不会报错，只会让报表"看起来不太对"。
    """
    text = str(raw or "").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:COMPANY_NAME_MAX]


async def resolve_company_id(db: AsyncSession, company_name, city=None):
    """按公司名查/建 `company` 记录，返回其 id；公司名为空时返回 None。

    **重名规则（这是本函数唯一需要判断的地方，改动前先读完）**

    1. **唯一性按名称判定，不区分城市。** 同一家公司在多个城市招人
       （字节跳动 北京/上海/深圳）必须归为同一家公司，否则「按公司聚合岗位数」
       会把它拆成三份，公司排行榜直接失真。
    2. **代价**：同名的两家不同公司会被合并（例如两家都叫「云图科技」的空壳公司）。
       招聘数据里这是**可接受**的取舍 —— 误合并只影响少量同名的中小公司，
       而按 (名称, 城市) 拆表会把大公司的跨城招聘**系统性**拆散，后者伤害更大。
    3. **创建时把本次岗位城市记为 `Company.city`**，它只代表「首次出现的城市」，
       **不是公司总部**，不要拿它做总部判断。
    4. **库里若已有同名多条**（历史数据或并发写入造成），**不静默取第一条**：
       优先取城市匹配的那条，否则取 id 最小者，并打 warning 列出全部候选 id ——
       让「数据分裂」这件事在日志里可见，而不是被悄悄吞掉。

    返回 None 表示这条岗位没有公司名，调用方应把 `company_id` 留空。
    """
    name = normalize_company_name(company_name)
    if not name:
        return None

    rows = (await db.execute(select(Company).where(Company.name == name))).scalars().all()
    if rows:
        if len(rows) == 1:
            return rows[0].id
        same_city = [c for c in rows if (c.city or "") == (city or "")]
        chosen = (same_city or sorted(rows, key=lambda c: c.id))[0]
        logger.warning(
            "公司名 %s 在 company 表有 %s 条重复记录（ids=%s），本次使用 id=%s（依据：%s）",
            name, len(rows), [c.id for c in rows], chosen.id,
            "城市匹配" if same_city else "最小 id",
        )
        return chosen.id

    company = Company(name=name, city=(city or None))
    db.add(company)
    # flush 而不是 commit：拿自增 id，同时把提交时机交还给调用方（与 save_job
    # 的现有事务边界一致，避免这里偷偷提交掉半批数据）。
    await db.flush()
    return company.id


async def save_job(db: AsyncSession, job_data: dict) -> bool:
    """保存单个岗位。已存在则只刷新状态与 last_seen_at，返回 False。

    :param job_data: 必须已过 :func:`normalize_job`（含 min_salary / max_salary）。
        这里仍用 ``.get`` 兜底而不是下标：一次字段缺失不该让整批 50 条全部回滚，
        但缺字段本身要留 warn 以便定位是哪个爬虫没遵守契约。

    副作用：会按 ``company_name`` 建/复用 ``Company`` 并回填 ``company_id``
    （规则见 :func:`resolve_company_id`）。这是所有入库路径的唯一汇合点，
    因此公司实体只在这里维护一份。
    """
    # 来源校验放在任何写库动作之前：来源不明的数据宁可拒绝，也不落库。
    # 拒绝时留 error 日志并抛 ValueError —— 静默写一个空来源或默认平台，
    # 会让「演示数据 vs 真实采集」这条边界永久失效（这是本项的修复目标）。
    try:
        source_site = normalize_source_site(job_data.get("source_site"))
    except ValueError as e:
        logger.error(
            "岗位入库被拒（job_key=%s，title=%s）：%s",
            job_data.get("job_key"), job_data.get("title"), e,
        )
        raise

    existing = await db.execute(select(Job).where(Job.job_key == job_data["job_key"]))
    job = existing.scalar_one_or_none()
    if job:
        job.job_status = "ACTIVE"
        job.last_seen_at = _utc_now()
        # 存量岗位的 company_id 为空时顺手补上：重爬一次就自愈，不必等运维
        # 手动跑回填脚本。规则与新建分支完全相同（同一个 resolve_company_id）。
        if job.company_id is None:
            job.company_id = await resolve_company_id(
                db,
                job.company_name or job_data.get("company_name"),
                job.city or job_data.get("city"),
            )
        return False

    job = Job(
        title=job_data.get("title", ""),
        company_name=job_data.get("company_name", ""),
        # 公司实体在这里建/复用 —— 这是全部入库路径（服务端爬虫、浏览器上报、
        # 声明式数据源）的唯一汇合点，所以只在这一处做即可。
        company_id=await resolve_company_id(
            db, job_data.get("company_name"), job_data.get("city")
        ),
        source_site=source_site,
        job_key=job_data["job_key"],
        job_status="NEW",
        city=job_data.get("city", ""),
        experience=job_data.get("experience", ""),
        education=job_data.get("education", ""),
        min_salary=job_data.get("min_salary") or 0,
        max_salary=job_data.get("max_salary") or 0,
        skills=job_data.get("skills", ""),
        url=job_data.get("url", ""),
        job_desc=job_data.get("description", ""),
        last_seen_at=_utc_now(),
    )
    db.add(job)
    return True


async def ingest_browser_jobs(db: AsyncSession, jobs: list, source_site: str, city: str) -> dict:
    """接收浏览器端（油猴脚本 / 扩展）采集到的岗位并入库。

    与爬虫共用**同一条**清洗、去重、入库链路，但有四处刻意不同：

    1. **不触发下架判定**。浏览器采集是「用户浏览到哪就采到哪」的部分数据，
       拿它去判断「哪些岗位没再出现」会把库里其它岗位整批误判下架。
       （爬虫按「关键词 × 城市」系统性抓取、覆盖范围已知，所以它可以判断下架。）
    2. **不消费日配额**。配额是给自动爬虫设的防封闸门；浏览器采集走用户真实 IP
       与真实指纹，不存在「集中访问」这个特征。但仍受单次批量上限约束 ——
       那一道是防脚本写错一次灌进几万条。
    3. **必须沿用同一个平台标识**（如 ``boss`` / ``51job``），不要造 ``boss-browser``。
       因为 ``job_key`` 由「平台 + 标题 + 公司 + 城市」生成，换平台名会让同一个岗位
       在库里出现两行 —— 爬虫采一次、浏览器采一次，统计随之翻倍。
    4. **``job_key`` 与过滤规则都由服务端决定**。浏览器端只做「哑采集器」，
       只负责从 DOM 里取原始文本；业务口径（指纹算法、薪资解析、技能抽取、
       过滤规则）单一来源留在服务端，否则两个入口的口径必然漂移。
    """
    result = {"received": len(jobs or []), "saved": 0, "duplicate": 0, "filtered": 0, "invalid": 0}

    # 整批来源先校验：来源为空 / 未知时**一条都不入**，并留一条 error 日志。
    # 为什么整批而不是逐条：逐条会打印 N 条同样的日志，反而把真正的原因淹没；
    # 而「平台传错」本来就是调用方的整批参数错误，不是某条数据的属性。
    try:
        source_site = normalize_source_site(source_site)
    except ValueError as e:
        logger.error("浏览器上报整批被拒（received=%s）：%s", result["received"], e)
        result["invalid"] = result["received"]
        return result

    for raw in jobs or []:
        if not isinstance(raw, dict):
            result["invalid"] += 1
            continue

        title = str(raw.get("title") or "").strip()
        if not title:
            # 没有标题的条目无法生成指纹，也无法展示，直接计为无效
            result["invalid"] += 1
            continue

        cleaned = clean_job_data({
            "title": title,
            "company_name": str(raw.get("company_name") or "").strip(),
            "city": str(raw.get("city") or city or "").strip(),
            "experience": str(raw.get("experience") or "").strip(),
            "education": str(raw.get("education") or "").strip(),
            "salary": str(raw.get("salary") or "").strip(),
            "skills": str(raw.get("skills") or "").strip(),
            "source_site": source_site,
            "url": str(raw.get("url") or "").strip(),
            "description": str(raw.get("description") or "").strip(),
        })
        cleaned["job_key"] = generate_job_key(
            source_site, cleaned["title"], cleaned["company_name"], cleaned["city"]
        )

        # 与爬虫同一套三道过滤，保证两个入口的数据口径一致
        if (
            is_senior_job(cleaned["title"], cleaned["experience"])
            or is_invalid_job(cleaned["title"], cleaned["description"])
            or is_high_salary(cleaned["min_salary"])
        ):
            result["filtered"] += 1
            continue

        try:
            if await save_job(db, cleaned):
                result["saved"] += 1
            else:
                result["duplicate"] += 1
        except ValueError as e:
            # 单条被 save_job 拒绝（例如来源在清洗后变空）：拒绝这一条，
            # 但不让整批回滚 —— 浏览器一次可能上报几百条，一条脏数据不该拖累全部。
            result["invalid"] += 1
            logger.warning("浏览器上报条目被拒：%s", e)

    await db.commit()
    return result


async def _crawl_platform(db: AsyncSession, keyword: str, city: str, platform: str) -> tuple:
    """抓取并入库单个平台。

    返回 (新增条数, 本次抓到的 job_key 列表, 说明条目)。
    说明条目用于解释「抓到了但没入库」的情况（generic 数据源 / 缺 job_key），
    会原样并入任务 message —— 否则前端只看到「已完成 / 0 条」，无从判断原因。

    在真正发起请求之前依次过三道「任务之间」的闸：
    合规（robots）→ 配额（今日还剩多少次）→ 域名节流（同一域名串行且保底间隔）。
    这三道闸解决的是 ``BaseCrawler`` 覆盖不到的盲区（它只管一次爬取内部的间隔）。
    """
    crawler = get_crawler(platform)
    probe = probe_url(platform)

    if probe:
        # 1) 合规：robots.txt。宽松模式下只告警但说明会落到日志/message，不静默通过
        allowed, note = await robots.gate(platform, probe)
        if not allowed:
            raise RuntimeError(note)
        if note:
            logger.warning("平台 %s 合规提示：%s", platform, note)

        # 2) 配额：超限时明确失败，而不是继续把额度耗干。
        #    用 raise 而不是 return 0 —— 「0 条」和「被配额拦下」是两回事，
        #    后者必须让使用者看见原因。
        limit = settings.crawl_daily_quota_per_platform
        if not daily_quota.try_consume(platform, limit):
            raise RuntimeError(
                f"平台 {platform} 已达今日采集配额（{limit} 次/日，按北京日期计）。"
                f"如需调整请修改 CRAWL_DAILY_QUOTA_PER_PLATFORM。"
            )

        # 3) 域名节流：同一域名的请求串行化 + 最小间隔（跨任务生效）
        waited = await await_domain_slot(probe, settings.crawl_domain_min_interval)
        if waited:
            logger.info("平台 %s 域名节流等待 %.1fs", platform, waited)

    # 走带指数退避的 retry 包装，而不是裸调用 crawl()：
    # 否则目标站点一次抖动就会让整个任务 FAILED。
    jobs = await crawler.crawl_with_retry(keyword, city)

    # 声明式数据源可能是 generic（分析用）或缺少 job_key 的行。
    # ``deduplicate_jobs`` 只保留有 job_key 的条目 —— 不先把这两类挑出来，
    # 它们会被静默吞掉，症状是「YAML 配好了、抓到了、库里一条没有」。
    notes: list = []
    # 详情页抓取的失败条数 / 风控即停 / 预算截断由执行器自行记录，
    # 这里并进同一条任务 message —— 不另造返回结构。
    notes.extend(getattr(crawler, "detail_notes", []) or [])
    # 爬虫报告「被风控/登录页拦下」时必须把原因写进任务说明。
    # 否则任务显示「已完成 / 0 条」，看起来像"这批关键词没数据"，
    # 实际是被拦了 —— 这类静默失败最难排查（2026-11-03 在 BOSS 上实测到此形态）。
    blocked_reason = getattr(crawler, "blocked_reason", "") or ""
    if blocked_reason:
        notes.append(f"平台 {platform} 未取到岗位：{blocked_reason}")
    generic_rows = [j for j in jobs if j.get("_target") == "generic"]
    if generic_rows:
        notes.append(
            f"平台 {platform} 是通用（generic）数据源：本次抓到 {len(generic_rows)} 条"
            f"分析用数据，未写入岗位表"
        )
    job_rows = [j for j in jobs if j.get("_target") != "generic"]
    missing_key = [j for j in job_rows if not j.get("job_key")]
    if missing_key:
        notes.append(
            f"平台 {platform} 有 {len(missing_key)} 条记录缺少 job_key，无法去重入库已跳过"
        )
    jobs = deduplicate_jobs(job_rows)

    saved = 0
    seen_keys: list = []

    for job_data in jobs:
        # 各爬虫输出契约不一致（boss 只给 salary，job51 已算好 min/max），
        # 入库前统一补齐 —— 否则 boss 的新岗位全部 KeyError。
        job_data = normalize_job(job_data)
        title = job_data.get("title", "")
        experience = job_data.get("experience", "")
        description = job_data.get("description", "")
        min_salary = job_data.get("min_salary", 0)

        # 三道清洗规则：高级岗位 / 无效岗位（培训、外包、博彩等）/ 薪资异常
        if is_senior_job(title, experience):
            continue
        if is_invalid_job(title, description):
            continue
        if is_high_salary(min_salary):
            continue

        if not job_data.get("skills"):
            job_data["skills"] = extract_skills(title, description)

        seen_keys.append(job_data["job_key"])

        if await save_job(db, job_data):
            saved += 1
            if saved % 50 == 0:
                await db.commit()

    await db.commit()
    return saved, seen_keys, notes


async def _mark_offline(
    db: AsyncSession,
    seen_keys: list,
    platforms: list,
    city: str,
    crawl_started_at,
) -> None:
    """把「本次确实爬过、但已长期未再出现」的岗位置为 OFFLINE。

    两个保护条件：
    1) 只处理本次实际爬取的平台，避免伤及无关数据；
    2) 额外要求 last_seen_at 早于「本次任务开始时间 - 宽限期」。
       原因是 Job 表没有记录关键词，一次「Java/北京」的爬取无法证明
       「Python/北京」的岗位已下架；用宽限期保证最近被其它关键词任务
       刷新过的岗位不会被误判，只有确实长期没再出现的才置为 OFFLINE。
    """
    grace_cutoff = crawl_started_at - timedelta(seconds=OFFLINE_GRACE_SECONDS)
    conditions = [
        Job.job_key.notin_(seen_keys),
        Job.source_site.in_(platforms),
        Job.job_status.in_(["NEW", "ACTIVE"]),
        or_(Job.last_seen_at.is_(None), Job.last_seen_at < grace_cutoff),
    ]
    if city:
        conditions.append(Job.city == city)

    await db.execute(update(Job).where(*conditions).values(job_status="OFFLINE"))
    await db.commit()


async def create_pending_task(db: AsyncSession, keyword: str, city: str, platforms: list) -> CrawlTask:
    """仅创建一条待执行的任务记录，不执行爬取。

    供「先建任务、后台异步执行」的调用方使用 —— 真实爬取要翻多页、
    每页间隔 12~18 秒，同步执行必然撑爆 HTTP 超时。

    平台标识在这里就归一化后入库：任务列表展示、下架判定、日志排查都读这个字段，
    存入未归一化的值（如「BOSS直聘」）会让同一平台在不同任务里长得不一样。
    """
    platforms = normalize_platforms(platforms)
    task = CrawlTask(
        source_site=",".join(platforms),
        keyword=keyword,
        city=city,
        status="PENDING",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _mark_task_failed(db: AsyncSession, task_id: int, exc: BaseException) -> None:
    """把任务置为 FAILED —— **保证 session 已不可用时也写得进去**。

    为什么需要这个函数（原来直接在 except 里 commit）：
    抛异常的地方通常就在同一 session 的 flush/commit 上（唯一约束冲突、
    列宽超限、SQLite readonly）。此时 session 已被标记 needs-rollback，
    再 commit 会抛 ``PendingRollbackError``：状态根本没写进库，任务永久停在
    ``RUNNING``（前端只会显示"进行中"，永远不会结束），而且向上抛出的是这个
    新异常，``str(e)`` 里的原始原因被替换掉，排查时看不到真实报错。

    所以：先 rollback 让 session 可用，再写状态；连 rollback 都救不回来时，
    用独立会话兜底（与 ``log_action`` 同一思路）。
    """
    reason = (str(exc) or exc.__class__.__name__)[:1000]

    try:
        await db.rollback()
        task = await db.get(CrawlTask, task_id)
        if task is not None:
            task.status = "FAILED"
            task.message = reason
            await db.commit()
            return
    except Exception:
        logger.warning("任务 %s 状态回写失败，改用独立会话兜底", task_id, exc_info=True)

    try:
        async with _get_failure_session_factory()() as session:
            task = await session.get(CrawlTask, task_id)
            if task is not None:
                task.status = "FAILED"
                task.message = reason
                await session.commit()
                return
    except Exception:
        logger.error(
            "任务 %s 最终状态回写失败，将停留在 RUNNING，需人工修正为 FAILED：%s",
            task_id,
            reason,
            exc_info=True,
        )


async def run_crawl(
    db: AsyncSession,
    task: CrawlTask,
    keyword: str,
    city: str,
    platforms: list,
) -> int:
    """在给定任务记录上执行爬取，返回本次新增岗位数。

    状态流转：RUNNING → COMPLETED / FAILED（失败写入 message 并向上抛）。
    可由请求线程同步调用，也可由后台任务 / 定时任务复用。
    """
    task.status = "RUNNING"
    await db.commit()
    # 先把 id 取出来：后面 session 一旦需要 rollback，task 实例会过期，
    # 再访问 task.id 会触发一次 IO，而此时连接可能正是坏的那个。
    task_id = task.id

    # ---- 前置校验：把「必然失败」的请求拦在爬取之前 ----
    # 这些失败重试不会变好，却要白等 4 次指数退避（2+4+8 秒），
    # 并且原实现会把任务标成 COMPLETED / 0 条且不带任何说明，无从排查。
    # 同样归一化：run_crawl 是执行层的唯一入口，不能假设调用方已经处理过
    # （定时任务、后台重跑、直接调用都会经过这里）
    requested = normalize_platforms(platforms)
    runnable = [p for p in requested if p in SUPPORTED_PLATFORMS]
    skipped = [p for p in requested if p not in SUPPORTED_PLATFORMS]
    skipped_notes: list = []
    if skipped:
        skipped_notes.append(
            f"已跳过未实现平台：{', '.join(skipped)}"
            f"（当前已实现：{', '.join(sorted(SUPPORTED_PLATFORMS))}）"
        )

    if not runnable:
        task.status = "FAILED"
        task.message = (
            f"请求的平台均未实现：{', '.join(skipped) or '（未指定平台）'}。"
            f"当前已实现：{', '.join(sorted(SUPPORTED_PLATFORMS))}"
        )
        await db.commit()
        logger.warning("任务 %s 未执行：%s", task.id, task.message)
        return 0

    # 城市校验同理：未收录城市会抓到别的城市的数据，必须提前拦住
    from app.crawlers.city_map import UnsupportedCityError, require_city_code

    try:
        require_city_code(city)
    except UnsupportedCityError as e:
        task.status = "FAILED"
        task.message = str(e)
        await db.commit()
        logger.warning("任务 %s 未执行：%s", task.id, e)
        return 0

    crawl_started_at = _utc_now()
    try:
        total_saved = 0
        seen_keys: list = []
        crawled_platforms: list = []

        # 必须遍历 runnable（已归一化 + 已过滤）而不是原始 platforms：
        # 传「BOSS直聘」时 normalize_platforms 会得到 "boss"，但原始列表里仍是中文，
        # `platform not in SUPPORTED_PLATFORMS` 恒真 → 全部跳过 → 任务 COMPLETED / 0 条，
        # 且不写任何说明，前端只看到「已完成」。归一化了却不用，等于没归一化。
        for platform in runnable:
            if platform not in SUPPORTED_PLATFORMS:
                logger.warning("平台 %s 尚未实现，跳过", platform)
                continue
            saved, keys, platform_notes = await _crawl_platform(db, keyword, city, platform)
            total_saved += saved
            seen_keys.extend(keys)
            skipped_notes.extend(platform_notes)
            crawled_platforms.append(platform)

        # 本次一条岗位都没抓到（例如全部被清洗规则过滤）时不触发下架判定，
        # 否则会因「本次结果为空」而把存量岗位整批误判下架。
        if seen_keys and crawled_platforms:
            await _mark_offline(db, seen_keys, crawled_platforms, city, crawl_started_at)

        task.status = "COMPLETED"
        task.job_count = total_saved
        # 被跳过的平台必须写进 message：否则前端只会看到「已完成 / 0 条」，
        # 完全不知道是因为平台没实现（原实现就是这样静默吞掉的）。
        task.message = ("；".join(skipped_notes) or None) if skipped_notes else None
        await db.commit()
        return total_saved
    except Exception as e:
        # 不在这里直接 commit：抛异常的位置很可能就是同一 session 的 flush，
        # session 已 needs-rollback，直接 commit 会抛 PendingRollbackError，
        # 把「任务 FAILED」这件事本身也搞丢（见 _mark_task_failed 的说明）。
        await _mark_task_failed(db, task_id, e)
        raise


async def start_crawl_task(db: AsyncSession, keyword: str, city: str, platforms: list) -> CrawlTask:
    """创建任务并**同步**执行，返回**任务对象**。

    与 ``start_crawl`` 的区别只在于返回值：HTTP 接口需要把失败原因（例如
    「平台未实现」「城市未收录」）一并回给调用方，只返回一个 count 会让用户
    看到「成功 / 0 条」而完全不知道原因。
    """
    task = await create_pending_task(db, keyword, city, platforms)
    await run_crawl(db, task, keyword, city, platforms)
    return task


async def start_crawl(db: AsyncSession, keyword: str, city: str, platforms: list) -> int:
    """创建任务并**同步**执行，返回本次新增岗位数。

    原生 HTTP 接口（POST /api/crawler/start）与定时任务走这里：
    调用方需要「返回时数据已入库」。批量导入场景请改用 create_pending_task + 后台执行。
    """
    task = await start_crawl_task(db, keyword, city, platforms)
    return task.job_count or 0


async def get_tasks(db: AsyncSession):
    result = await db.execute(select(CrawlTask).order_by(CrawlTask.created_at.desc()))
    return result.scalars().all()


async def get_task(db: AsyncSession, task_id: int):
    result = await db.execute(select(CrawlTask).where(CrawlTask.id == task_id))
    return result.scalar_one_or_none()
