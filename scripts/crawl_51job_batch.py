"""按「关键词 × 城市」批量跑 51job 采集任务（走既有服务层，不绕过任何业务规则）。

**为什么需要这个脚本**：51job 的搜索页 ``pageNum`` 参数不生效（实测第 2 页与第 1 页
重叠 16/20），单个任务最多只能拿到约 20 条。要攒到几百条真实岗位，只能换
关键词/城市组合多跑几次 —— 手动点几十次界面既慢又容易漏。

**它只是薄封装**：直接调用 ``crawler_service.start_crawl_task``，因此
清洗（高级岗位 / 无效岗位 / 高薪异常）、去重、``company`` 建实体、
``source_site`` 校验、``crawl_task`` 落库**全部走产品自己的规则**，
脚本里没有第二套实现。这是刻意的 —— 一旦脚本自己写库，统计口径立刻失真。

用法：
    # 看会跑哪些组合（不发任何请求）
    python scripts/crawl_51job_batch.py --dry-run

    # 跑默认组合（12 个关键词 × 5 个城市里的一部分）
    python scripts/crawl_51job_batch.py

    # 只跑指定的几对
    python scripts/crawl_51job_batch.py --pairs "python:长沙,java:北京,前端:深圳"

环境：
    DB_TYPE=sqlite  DB_NAME=data/recsys.db          # 或直接给绝对路径，写哪库很清楚
    CRAWL_DOMAIN_MIN_INTERVAL=8                     # 同域名保底间隔（秒），默认 20
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 非标准流时忽略
    pass

from app.database import async_session, engine  # noqa: E402
from app.services import crawler_service  # noqa: E402

# 默认组合：覆盖技术岗主力方向 × 一线/新一线城市。
# 只列 51job 已收录的城市（CITY_AREA_MAP），否则任务会因「城市未收录」直接失败。
DEFAULT_KEYWORDS = ["python", "java", "前端", "后端", "数据分析", "测试", "运维", "算法"]
DEFAULT_CITIES = ["长沙", "北京", "上海", "深圳", "杭州"]


def build_pairs(pairs_arg: str = "") -> list:
    if pairs_arg:
        result = []
        for chunk in pairs_arg.split(","):
            if not chunk.strip():
                continue
            keyword, _, city = chunk.partition(":")
            result.append((keyword.strip(), city.strip()))
        return result
    return [(k, c) for k in DEFAULT_KEYWORDS for c in DEFAULT_CITIES]


async def main() -> None:
    parser = argparse.ArgumentParser(description="批量跑 51job 采集任务（走服务层）")
    parser.add_argument("--pairs", default="", help="逗号分隔的 关键词:城市，留空用默认组合")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几对（0 = 不限）")
    parser.add_argument("--dry-run", action="store_true", help="只打印组合，不发请求")
    args = parser.parse_args()

    pairs = build_pairs(args.pairs)
    if args.limit:
        pairs = pairs[: args.limit]

    print(f"计划跑 {len(pairs)} 个采集任务：")
    for keyword, city in pairs:
        print(f"  - {keyword} / {city}")
    if args.dry_run:
        return

    ok = failed = 0
    total_saved = 0
    try:
        async with async_session() as db:
            for index, (keyword, city) in enumerate(pairs, start=1):
                try:
                    task = await crawler_service.start_crawl_task(
                        db, keyword, city, ["51job"]
                    )
                    saved = task.job_count or 0
                    total_saved += saved
                    ok += 1
                    note = f"（{task.message}）" if task.message else ""
                    print(
                        f"[{index}/{len(pairs)}] {keyword}/{city}: {task.status} "
                        f"新增 {saved} 条{note}"
                    )
                except Exception as exc:  # noqa: BLE001 - 单个任务失败不该中断整批
                    failed += 1
                    print(f"[{index}/{len(pairs)}] {keyword}/{city}: 失败 {type(exc).__name__}: {exc}")
                    await db.rollback()

            stats = await crawler_service.count_jobs_by_source(db)
    finally:
        await engine.dispose()

    print("-" * 60)
    print(f"任务完成 {ok} 个，失败 {failed} 个，本次新增 {total_saved} 条")
    print(f"库内来源分布：{stats['by_source']}")
    print(f"合计 {stats['total']}｜真实 {stats['real']}｜演示 {stats['demo']}｜来源不明 {stats['unknown']}")


if __name__ == "__main__":
    asyncio.run(main())
