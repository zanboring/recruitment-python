# -*- coding: utf-8 -*-
"""清除演示用的种子岗位数据（seed），把库腾给真实采集。

为什么需要单独一个脚本
----------------------
``scripts/seed_demo_data.py`` 只负责「灌」不负责「排」：它按 ``job_key`` 幂等插入，
没有清理入口。而真实采集要上线的第一步，是把 196 条画像合成的 seed 岗位清掉 ——
否则真实数据和合成数据混在一起，任何统计口径都不成立（面试时也说不清
「这些岗位哪来的」）。

安全约定
--------
- **默认 dry-run**：只统计、不删除。必须显式给 ``--apply`` 才动数据。
- 只删 ``source_site == 'seed'`` 的行。真实采集来的（``boss`` / ``51job`` /
  ``vision`` / ``userscript`` 等）一律不动。
- 不碰 ``company`` / ``knowledge_base`` / ``user`` / ``sys_log`` 等其它表。
- 执行前请先备份库文件（脚本会把目标库路径打印出来，便于复制）。

用法
----
    python scripts/purge_seed_data.py                # 预览（默认）
    python scripts/purge_seed_data.py --apply        # 真正删除
    python scripts/purge_seed_data.py --apply --yes  # 跳过二次确认（脚本化用）
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app.database import DATABASE_URL, async_session  # noqa: E402
from app.models import Job  # noqa: E402

SEED_SOURCE = "seed"


async def _counts(session) -> dict:
    """当前按 source_site 分组的行数。"""
    rows = await session.execute(
        select(Job.source_site, func.count()).group_by(Job.source_site)
    )
    return {(site or "<空>"): cnt for site, cnt in rows.all()}


async def _run(apply: bool, skip_confirm: bool) -> int:
    async with async_session() as session:
        before = await _counts(session)
        total = sum(before.values())
        seed_n = before.get(SEED_SOURCE, 0)

        print(f"数据库: {DATABASE_URL}")
        print(f"job 总数: {total}")
        print("按来源分布:")
        for site, cnt in sorted(before.items(), key=lambda kv: -kv[1]):
            mark = "  ← 将删除" if site == SEED_SOURCE else ""
            print(f"  {site:>12}: {cnt}{mark}")

        if seed_n == 0:
            print("\n没有 seed 数据，无需清理。")
            return 0

        if not apply:
            print(f"\n[dry-run] 本次将删除 {seed_n} 条 seed 岗位，其余表不动。")
            print("确认无误后加 --apply 执行。")
            return 0

        if not skip_confirm:
            # 非交互环境（CI / 管道）下不要卡住
            if not sys.stdin.isatty():
                print("\n非交互环境，请显式加 --yes 才会执行删除。")
                return 2
            answer = input(f"\n确认删除 {seed_n} 条 seed 岗位？输入 yes 继续: ").strip()
            if answer != "yes":
                print("已取消。")
                return 1

        result = await session.execute(delete(Job).where(Job.source_site == SEED_SOURCE))
        await session.commit()

        after = await _counts(session)
        remaining = sum(after.values())
        print(f"\n已删除 {result.rowcount} 条 seed 岗位。")
        print(f"job 剩余总数: {remaining}")
        if after:
            print("剩余来源分布:")
            for site, cnt in sorted(after.items(), key=lambda kv: -kv[1]):
                print(f"  {site:>12}: {cnt}")
        else:
            print("job 表已清空 —— 等待真实采集写入。")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="清除 seed 演示岗位（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="真正执行删除")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args()
    return asyncio.run(_run(args.apply, args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
