"""岗位存活核查：周期性打开已存岗位 URL，判断岗位是否仍在线。

**为什么需要它（数据保质）**：招聘数据有时效性 —— 岗位会下架、公司会改需求。
只入库不核查，库里躺着大量「已经不存在」的岗位，分析结论会失真。
核查器让系统自己维护数据新鲜度：启动后低速扫库，逐个打开已采集的
URL，凭「下线特征」判断岗位是否还在，命中则标记 OFFLINE。

稳定性设计（用户明确要求，重中之重）：
1. **不阻塞启动**：核查在 lifespan 内以 asyncio 后台任务启动，startup 立即返回；
2. **限速错峰**：每两条之间的延迟在 `checker_interval` 区间内随机（默认 8~15s），
   避免对目标站点形成突发流量而被风控；
3. **异常隔离**：单条失败只记日志继续下一条，绝不因一条异常中断整轮；
4. **进度持久化**：每轮最多处理 `checker_batch_size` 条，从「last_checked_at 最旧」
   的 ACTIVE 岗位开始，跑完一轮等 `checker_round_interval` 小时再跑下一轮；
5. **完全可选**：`JOB_CHECKER_ENABLED=false` 可整体关闭，默认开启但批少+慢速。
"""
import asyncio
import logging
import random
import time

from sqlalchemy import select, update

from app.config import settings
from app.models.job import Job
from app.utils.timeutil import utc_now

logger = logging.getLogger(__name__)

# 页面文本中出现任一特征即视为岗位已下线
OFFLINE_SIGNALS = (
    "职位已下线", "该职位已暂停", "招聘已结束", "职位不存在",
    "岗位已关闭", "停止招聘", "已下线", "职位过期",
    "job not found", "position closed", "this job is no longer available",
)
# 出现任一「仍是该岗位」的特征则判定在线（部分站点下架页可能只给个“返回首页”）
ALIVE_SIGNALS = ("刷新", "重新打开", "该职位在招", "投递简历")


def detect_offline(text: str) -> bool:
    """判断页面文本是否呈现「岗位已下线」特征；空文本视为不可判定（保守在线）。"""
    if not text:
        return False
    low = text.lower()[:3000]
    if any(a in low for a in ALIVE_SIGNALS):
        return False
    return any(s.lower() in low for s in OFFLINE_SIGNALS)


async def check_job_url(job: Job) -> bool:
    """核查单条岗位：返回 True 表示判定为已下线。

    用 httpx 轻量请求（详情页多为静态可读），失败视为不可判定（False），
    不因网络抖动把在线岗位误判为下线。
    """
    import httpx

    url = (job.url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False  # 无 URL 无法核查，保持现状

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
    except Exception as e:
        logger.debug("核查 %s 请求失败（不计为下线）：%s", url, e)
        return False

    if resp.status_code in (404, 410):
        return True
    if resp.status_code != 200:
        return False
    return detect_offline(resp.text)


async def run_sweep(db) -> dict:
    """执行一轮核查：取 last_checked_at 最旧、ACTIVE 且有 URL 的岗位，逐条核查。"""
    batch = settings.job_checker_batch_size
    # NULL（从未核查）在 SQLite/MySQL 默认排最前，正符合「先核查没查过的」；
    # 排序键 last_checked_at 在三种数据库方言下都可用。
    rows = (await db.execute(
        select(Job).where(
            Job.job_status == "ACTIVE",
            Job.url.isnot(None),
            Job.url != "",
        ).order_by(Job.last_checked_at.asc()).limit(batch)
    )).scalars().all()

    result = {"checked": 0, "offline": 0, "skipped": 0}
    checked_ids = []

    for job in rows:
        offline = await check_job_url(job)
        result["checked"] += 1
        checked_ids.append(job.id)
        if offline:
            result["offline"] += 1
            await db.execute(
                update(Job).where(Job.id == job.id)
                .values(job_status="OFFLINE", last_checked_at=utc_now())
            )
        # 记录核查时间（无论判定如何，避免每次轮询都盯同一批）
        await db.execute(
            update(Job).where(Job.id == job.id).values(last_checked_at=utc_now())
        )
        if len(checked_ids) % 5 == 0:
            await db.commit()
        # 限速错峰：随机延迟，防止突发请求
        await asyncio.sleep(random.uniform(settings.checker_delay_min, settings.checker_delay_max))

    await db.commit()
    logger.info("岗位核查完成：%s 条，判定下线 %s 条", result["checked"], result["offline"])
    return result


async def checker_loop():
    """后台核查循环：启动即跑一轮，之后每 round_interval 小时一轮（幂等锁由调度保证）。"""
    from app.database import async_session

    logger.info("岗位存活核查器启动（批次 %s，间隔 %ss~%ss）",
                settings.job_checker_batch_size, settings.checker_delay_min, settings.checker_delay_max)
    while True:
        try:
            async with async_session() as db:
                await run_sweep(db)
        except Exception as e:  # noqa: BLE001 - 核查失败绝不影响主服务
            logger.error("本轮岗位核查异常（下一轮继续）：%s", e, exc_info=True)
        await asyncio.sleep(settings.job_checker_round_interval * 3600)


def start_checker() -> "asyncio.Task | None":
    """在应用启动时启动核查后台任务（不阻塞启动）。"""
    if not getattr(settings, "job_checker_enabled", True):
        logger.info("岗位核查已关闭（JOB_CHECKER_ENABLED=false）")
        return None
    return asyncio.create_task(checker_loop())
