"""回填 `job.company_id`（幂等，可重复执行）。

**为什么需要这个脚本**：`company` 表在很长一段时间里是空的 —— 入库链路只写
`job.company_name` 文本，从不建公司实体（根因见 `app/services/crawler_service.py`
里 `resolve_company_id` 上方的注释）。链路修好只能保证**新数据**正确，
库里已有的 196 条 seed 数据仍是 `company_id = NULL`，要靠一次性回填。

**幂等性**：只处理 `company_id IS NULL` 且 `company_name` 非空的岗位。
第二遍运行时待回填数为 0、`company` 行数不变。

**规则单一来源**：直接复用 `crawler_service.resolve_company_id`，
不在脚本里另写一份查/建逻辑 —— 两份规则必然漂移（今天脚本按名称查、
明天入库链路加了归一化，同一家公司就会分裂成两条）。

用法：
    python scripts/backfill_company_ids.py
    python scripts/backfill_company_ids.py --dry-run

环境：与主程序一致（`DB_TYPE` / `DB_NAME` 等），不需要额外参数。
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.database import async_session, engine  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.services.crawler_service import resolve_company_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("backfill_company_ids")


async def _counts(db) -> dict:
    return {
        "job": (await db.execute(select(func.count()).select_from(Job))).scalar_one(),
        "company": (await db.execute(select(func.count()).select_from(Company))).scalar_one(),
        "job_with_company": (
            await db.execute(select(func.count()).select_from(Job).where(Job.company_id.is_not(None)))
        ).scalar_one(),
        "pending": (
            await db.execute(
                select(func.count()).select_from(Job).where(
                    Job.company_id.is_(None),
                    Job.company_name.is_not(None),
                    Job.company_name != "",
                )
            )
        ).scalar_one(),
    }


async def backfill(dry_run: bool = False) -> int:
    """回填并返回本次处理的岗位数。"""
    async with async_session() as db:
        before = await _counts(db)
        logger.info(
            "回填前：job=%s，company=%s，job.company_id 非空=%s，待回填=%s",
            before["job"], before["company"], before["job_with_company"], before["pending"],
        )

        rows = (
            await db.execute(
                select(Job).where(
                    Job.company_id.is_(None),
                    Job.company_name.is_not(None),
                    Job.company_name != "",
                )
            )
        ).scalars().all()

        if dry_run:
            logger.info("[dry-run] 将处理 %s 条岗位，不写库", len(rows))
            by_name = {}
            for job in rows:
                by_name.setdefault(job.company_name, 0)
                by_name[job.company_name] += 1
            logger.info("[dry-run] 涉及 %s 个不同公司名", len(by_name))
            return 0

        touched = 0
        for job in rows:
            job.company_id = await resolve_company_id(db, job.company_name, job.city)
            touched += 1
        await db.commit()

        after = await _counts(db)
        logger.info(
            "回填后：job=%s，company=%s，job.company_id 非空=%s，待回填=%s",
            after["job"], after["company"], after["job_with_company"], after["pending"],
        )
        logger.info("本次处理 %s 条；新增公司 %s 个", touched, after["company"] - before["company"])

        # 抽样核对：5 条岗位的 company_id 指向的公司名是否与 job.company_name 一致
        sample = (await db.execute(select(Job).where(Job.company_id.is_not(None)).limit(5))).scalars().all()
        logger.info("抽样核对（job.company_name → company.name）：")
        for job in sample:
            company = (await db.execute(select(Company).where(Company.id == job.company_id))).scalar_one_or_none()
            actual = company.name if company else "<查不到>"
            flag = "OK" if actual == job.company_name else "不一致"
            logger.info("  job#%s %-28s → %-28s [%s]", job.id, job.company_name, actual, flag)

        return touched


async def main() -> None:
    parser = argparse.ArgumentParser(description="回填 job.company_id（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    try:
        await backfill(dry_run=args.dry_run)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
