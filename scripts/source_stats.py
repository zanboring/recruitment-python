"""按来源统计岗位数（只读）：回答「库里这批数据有多少是真的」。

**为什么需要这个脚本**：`job.source_site` 是区分演示数据与真实采集的唯一依据，
但在此之前没有任何**可执行**的入口能把它读出来 —— 图表面板把两者混在一起，
被问「这 196 条哪来的」时只能靠记忆答。`app/routers/` 不在本轮的改动授权内
（trae 在改），所以只提供「服务方法 + 脚本」这一条路径，不新增 HTTP 接口。

口径（与 `crawler_service.count_jobs_by_source` 完全一致，不在这里另算）：

- `demo`    = `source_site = 'seed'`，即 seed 脚本灌入的演示数据
- `real`    = 非 seed 且来源非空，即真实采集/上报入库的数据
- `unknown` = 来源为空，说明有数据绕过了来源校验（历史行或 bug），必须看见

用法：
    python scripts/source_stats.py
    python scripts/source_stats.py --json

环境：与主程序一致（`DB_TYPE` / `DB_NAME` 等），不需要额外参数。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session, engine  # noqa: E402
from app.services.crawler_service import DEMO_SOURCE_SITE, count_jobs_by_source  # noqa: E402


def _render(stats: dict) -> str:
    lines = ["来源分布（job.source_site → 岗位数）："]
    for source, count in sorted(stats["by_source"].items(), key=lambda kv: (-kv[1], kv[0])):
        label = "（空来源，需排查）" if not source else ("演示数据" if source == DEMO_SOURCE_SITE else "")
        lines.append(f"  {source or '<空>' :<16} {count:>6}  {label}".rstrip())
    lines.append(
        f"合计 {stats['total']} 条｜真实 {stats['real']}｜演示 {stats['demo']}｜来源不明 {stats['unknown']}"
    )
    if stats["unknown"]:
        lines.append("⚠️ 存在来源为空的岗位：这部分既不能算真实也不能算演示，需查明写入路径。")
    if not stats["real"]:
        lines.append("⚠️ 真实采集数据为 0 条：当前所有分析结论都建立在演示数据上。")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="按来源统计岗位数（只读）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于其它脚本消费")
    args = parser.parse_args()

    try:
        async with async_session() as db:
            stats = await count_jobs_by_source(db)
        print(json.dumps(stats, ensure_ascii=False, indent=2) if args.json else _render(stats))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
