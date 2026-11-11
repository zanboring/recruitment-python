# -*- coding: utf-8 -*-
"""岗位详情回填：把 ``job_desc`` / ``detail_html`` / ``publish_time`` 补进库。

**为什么要单独一个脚本**

``crawl`` 只抓列表页，正文留在详情页里。库里的 ``job_desc`` 平均只有 10 个字符
（标题 + 福利词），``detail_html`` 与 ``publish_time`` 全空。回填要跑真实的
外部请求、要能中途停下再续、还要能证明「没动别的列」，这些都不适合塞进
定时任务里。

**三条硬约束**

1. **只 UPDATE 三列**：``job_desc`` / ``detail_html`` / ``publish_time``。
   ``job_status`` 是存活核查的结论，回填改它等于伪造核查结果 —— 脚本在写入
   前后各读一次状态，发现变化就报错退出（见 :func:`_verify_status_unchanged`）。
2. **写库前先备份**：``data/recsys.db`` → ``data/recsys.db.bak-<时间戳>``。
   ``--dry-run`` 不备份也不写（没有写入就没有可回滚的东西）。
3. **可续跑**：成功的岗位号记进 ``data/backfill_detail_state.json``；
   下次带 ``--resume`` 就从上一批之后继续，不会把已经补好的再抓一遍。

**节流**

默认沿用 ``settings.crawl_domain_min_interval``（20 秒/域名），与爬虫同一套
域名闸门（``await_domain_slot``）。这是对目标站的礼貌措施，**不要为了跑得快**
把它调到 0 —— 详情页回填是「补数据」，不是压测。

用法::

    # 试跑 5 条，只看结果不写库
    python scripts/backfill_detail.py --limit 5 --dry-run

    # 正式回填 20 条
    python scripts/backfill_detail.py --limit 20

    # 接着上一批继续（跳过已成功的）
    python scripts/backfill_detail.py --limit 20 --resume

    # 在副本上试（不要拿 recsys.db 做实验）
    python scripts/backfill_detail.py --db data/test_copy.db --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.crawlers.detail_fetcher import MIN_DESC_LEN, DetailFetcher  # noqa: E402

logger = logging.getLogger("backfill_detail")

DEFAULT_DB = ROOT / "data" / "recsys.db"
DEFAULT_STATE = ROOT / "data" / "backfill_detail_state.json"

# 需要回填的判定：没有正文，或者正文短得明显不是正文（列表页拼出来的标题+福利词）。
# detail_html 为空也算 —— 有的岗位正文有了但整页 HTML 还没存。
_NEED_BACKFILL = (
    "(url IS NOT NULL AND TRIM(url) <> '') AND ("
    " job_desc IS NULL OR LENGTH(TRIM(job_desc)) < ?"
    " OR detail_html IS NULL OR TRIM(detail_html) = ''"
    ")"
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _pending_rows(conn: sqlite3.Connection, limit: int, skip_ids: set) -> list:
    sql = (
        "SELECT id, title, city, url, job_status,"
        "       LENGTH(COALESCE(job_desc, '')) AS desc_len"
        f"  FROM job WHERE {_NEED_BACKFILL}"
        "  ORDER BY id"
    )
    rows = [dict(r) for r in conn.execute(sql, (MIN_DESC_LEN,))]
    if skip_ids:
        rows = [r for r in rows if r["id"] not in skip_ids]
    return rows[:limit] if limit > 0 else rows


def _backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, target)
    return target


def _status_snapshot(conn: sqlite3.Connection, ids: list) -> dict:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        r["id"]: r["job_status"]
        for r in conn.execute(f"SELECT id, job_status FROM job WHERE id IN ({marks})", ids)
    }


def _write_one(conn: sqlite3.Connection, job_id: int, result) -> list:
    """只写这三列；没有值的列不出现在 SET 里（不拿空值覆盖已有内容）。"""
    sets, params = ["job_desc = ?"], [result.job_desc]
    if result.publish_time is not None:
        sets.append("publish_time = ?")
        params.append(result.publish_time.strftime("%Y-%m-%d %H:%M:%S"))
    if result.detail_html:
        sets.append("detail_html = ?")
        params.append(result.detail_html)
    params.append(job_id)
    conn.execute(f"UPDATE job SET {', '.join(sets)} WHERE id = ?", params)
    return sets


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"ok": [], "failed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"ok": [], "failed": {}}
    if not isinstance(data, dict):
        return {"ok": [], "failed": {}}
    data.setdefault("ok", [])
    data.setdefault("failed", {})
    return data


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _verify_status_unchanged(conn: sqlite3.Connection, before: dict) -> bool:
    after = _status_snapshot(conn, list(before))
    diff = {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)}
    if diff:
        logger.error("job_status 被改动！这是红线：%s", diff)
        return False
    return True


async def run(args) -> int:
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    if not db_path.exists():
        logger.error("数据库不存在：%s", db_path)
        return 2

    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = (ROOT / state_path).resolve()
    state = _load_state(state_path)
    skip_ids = {int(x) for x in state.get("ok", [])} if args.resume else set()

    conn = _connect(db_path)
    try:
        rows = _pending_rows(conn, args.limit, skip_ids)
        if not rows:
            print(f"没有待回填的岗位（库里已无缺失项，或 --limit 太小）。库：{db_path}")
            return 0

        print(f"待回填 {len(rows)} 条（库：{db_path}，dry-run={args.dry_run}，resume={args.resume}）")

        backup = None
        if not args.dry_run:
            backup = _backup(db_path)
            print(f"已备份：{backup}（{backup.stat().st_size} 字节）")

        ids = [r["id"] for r in rows]
        status_before = _status_snapshot(conn, ids)

        fetcher = DetailFetcher(min_interval=args.interval)
        ok = failed = skipped = 0
        try:
            for idx, row in enumerate(rows, 1):
                result = await fetcher.fetch(
                    row["url"], keyword=row["title"] or "", city=row["city"] or ""
                )
                if result.ok:
                    ok += 1
                    if not args.dry_run:
                        _write_one(conn, row["id"], result)
                        conn.commit()
                        state.setdefault("ok", []).append(row["id"])
                        state.get("failed", {}).pop(str(row["id"]), None)
                        _save_state(state_path, state)
                    print(
                        f"[{idx}/{len(rows)}] ok   id={row['id']} source={result.source} "
                        f"desc_len={len(result.job_desc)} "
                        f"publish={result.publish_time or '-'} "
                        f"html_len={len(result.detail_html)}"
                    )
                elif result.reason.startswith("缺少岗位 URL"):
                    skipped += 1
                    print(f"[{idx}/{len(rows)}] skip id={row['id']} 原因={result.reason}")
                else:
                    failed += 1
                    if not args.dry_run:
                        state.setdefault("failed", {})[str(row["id"])] = result.reason
                        _save_state(state_path, state)
                    print(f"[{idx}/{len(rows)}] fail id={row['id']} 原因={result.reason}")

                if idx % 10 == 0:
                    print(f"---- 进度 {idx}/{len(rows)}：成功 {ok} / 失败 {failed} / 跳过 {skipped}")
        finally:
            await fetcher.aclose()

        print(
            f"\n完成：成功 {ok} / 失败 {failed} / 跳过 {skipped}（共 {len(rows)} 条）"
            + ("（dry-run，未写库）" if args.dry_run else "")
        )
        if not _verify_status_unchanged(conn, status_before):
            return 3
        print("job_status 校验：所有被回填岗位的状态与回填前一致。")
        return 0
    finally:
        conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="回填岗位详情（job_desc / detail_html / publish_time）")
    parser.add_argument("--limit", type=int, default=20, help="本批最多处理多少条（默认 20）")
    parser.add_argument("--dry-run", action="store_true", help="只抓不写库（不备份）")
    parser.add_argument("--resume", action="store_true", help="跳过状态文件里已成功过的岗位")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="续跑状态文件路径")
    parser.add_argument(
        "--interval", type=float, default=None,
        help="同域名最小间隔（秒）；默认沿用 settings.crawl_domain_min_interval",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n已中断：成功写入的记录已提交，再跑时加 --resume 继续。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
