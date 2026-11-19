# -*- coding: utf-8 -*-
"""skills 重算：把已回填的正文变成可用的技能标签。

**为什么要单独一个脚本**

``skills`` 是入库时按「标题 + 当时那段很短的正文」算出来的。正文回填到
270/555 之后，技能列仍是旧值（副本实测：270 条完整正文里只有 15 条与
``extract_skills(title, job_desc)`` 一致）。推荐、技能画像、技能分布全读这一列，
不重算等于回填只落地了一半。

**四条硬约束**

1. **只 UPDATE ``skills`` 与 ``skills_sources`` 两列**。``skills`` 的语义不变
   （还是逗号串标签），``skills_sources`` 是 2026-11-24 新增的证据来源列。
   ``job_status`` / 薪资 / 标题 / 正文 / ``job_key`` / ``updated_at`` 一个字不碰。
   写前写后对其它列做一次指纹比对（:func:`_fingerprint`），不一致就报红线并返回退出码 3。
2. **覆盖而非合并**。库里的旧值与 ``extract_skills`` 同源，当年只是正文短所以少；
   集成端在副本上实测「变多 120 行 / 变少 0 行 / 相同 20 行」。
   脚本仍把「变多 / 变少 / 同数不同序」分开统计 —— 真出现变少也不会被
   一行「汇总成功」盖过去。
3. **写前备份，且备份必须完整**。直接复用 ``scripts/backfill_detail.py`` 的
   ``_backup()``（SQLite 在线备份接口 + ``PRAGMA integrity_check``，失败即删
   半截并中止），不复制一份实现。``--dry-run`` 不备份也不写。

**幂等**：``extract_skills`` 是纯函数，写进去的值再算一次完全相同 —— 第二次跑
应报「变更 0 条」。这也是判断脚本有没有写歪的最直接证据。

**汇总必出**：处理条数 / 变更条数 / 新增标签数 / 覆盖前后对比 / 耗时放在
``finally`` 里，写锁、异常、Ctrl-C 都不会把汇总丢掉。

用法::

    # 只看统计，不写库
    python scripts/recompute_skills.py --db data/_backfill_run.db --dry-run

    # 实跑（写前自动备份）
    python scripts/recompute_skills.py --db data/_backfill_run.db

    # 调试：只处理前 20 条
    python scripts/recompute_skills.py --db data/_backfill_run.db --limit 20 --dry-run

退出码：0 正常 / 1 未预期错误 / 2 库不存在 / 3 红线被碰（其它列被改动）/
5 备份失败 / 6 写锁等待超时 / 130 Ctrl-C。
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK：往 print 里塞一个 GBK 编不了的字符会抛
# UnicodeEncodeError。汇总在 finally 里，崩在收尾处等于整批统计白跑。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 非标准流（被重定向）时忽略
    pass

from app.crawlers.cleaner import extract_skills  # noqa: E402
from scripts.backfill_detail import (  # noqa: E402
    BackupError,
    _backup,
    _connect,
    _fmt_duration,
    _is_lock_error,
)

logger = logging.getLogger("recompute_skills")

DEFAULT_DB = ROOT / "data" / "recsys.db"

# 红线比对用的列：除了 ``skills`` 之外，凡是"重算不该碰"的都列进来。
# 刻意包含 ``updated_at``：拿它当"有没有顺手动过行"的哨兵。
_NON_SKILLS_COLUMNS = (
    "job_status",
    "title",
    "job_desc",
    "job_key",
    "min_salary",
    "max_salary",
    "url",
    "detail_html",
    "publish_time",
    "updated_at",
)


SOURCES_COLUMN = "skills_sources"


def _labels(value) -> list:
    """把逗号分隔的技能串拆成列表（去空白、去空项）。"""
    return [s.strip() for s in str(value or "").split(",") if s.strip()]


def encode_skill_sources(labels, title) -> str:
    """给 ``skills`` 的**每个**标签标一个来源，编码成 ``|title:X|desc:Y|``。

    为什么按标签逐条编码（而不是 ``title=...,...|desc=...``）：
    验收问题是「哪些标签来自标题」——逐条编码后，精确查询就是一个 LIKE：

        SELECT id FROM job WHERE skills_sources LIKE '%|title:Java|%'

    逗号分组的写法要先把整段取回再切字符串，在 SQL 里查不了。
    前后都带 ``|`` 是为了防止 ``title:Go`` 命中 ``title:Golang`` 这种假阳性。

    判定复用 ``cleaner._SKILL_MATCHERS``（抽取逻辑本轮**不改**，只是读一下
    哪些标签能从标题匹配到）。注意这是**只读引用**：改抽取规则仍只在 cleaner 一处。
    """
    from app.crawlers.cleaner import _SKILL_MATCHERS

    title_hits = {
        skill for skill, matcher in _SKILL_MATCHERS.items()
        if matcher.search(str(title or ""))
    }
    if not labels:
        return ""
    return "|" + "|".join(
        f"{'title' if label in title_hits else 'desc'}:{label}" for label in labels
    ) + "|"


def title_labels_from_sources(value) -> set:
    """解析出「标题级证据」的标签集合（验证与反查都用它）。"""
    return {
        part[len("title:"):] for part in str(value or "").split("|")
        if part.startswith("title:")
    }


def source_of_label(value, label: str) -> str:
    """查一个具体标签的来源：``title`` / ``desc`` / ``""``（未标注）。"""
    text = str(value or "")
    if f"|title:{label}|" in text:
        return "title"
    if f"|desc:{label}|" in text:
        return "desc"
    return ""


def _has_sources_column(conn: sqlite3.Connection) -> bool:
    return SOURCES_COLUMN in {row[1] for row in conn.execute("PRAGMA table_info(job)")}


def _ensure_sources_column(conn: sqlite3.Connection) -> bool:
    """老库上补列（SQLite）。返回是否真的补了。

    正常部署走 ``app/schema_sync.py`` 启动时自动加列；这里再兜一层，是因为
    重算脚本常被直接拿一个旧副本跑，那种情况下不能让 UPDATE 报 no such column。
    """
    if _has_sources_column(conn):
        return False
    conn.execute(f"ALTER TABLE job ADD COLUMN {SOURCES_COLUMN} VARCHAR(500)")
    conn.commit()
    return True


def _fingerprint(conn: sqlite3.Connection) -> tuple:
    """整表非 ``skills`` / ``skills_sources`` 列的指纹（行数 + 每行这些列的内容）。

    只用来证明"这次重算没碰别的列"，不追求密码学强度 —— 只要在几百行的规模上
    能区分"变了 / 没变"就够了。整表而不是只算被处理的行：``--limit`` 时
    处理范围之外的行也不该被改。
    """
    cols = ", ".join(_NON_SKILLS_COLUMNS)
    rows = conn.execute(f"SELECT id, {cols} FROM job ORDER BY id").fetchall()
    digest = hashlib.sha1(repr([tuple(r) for r in rows]).encode("utf-8")).hexdigest()
    return len(rows), digest


def _collect_changes(conn: sqlite3.Connection, limit: int, stats: dict) -> list:
    """算出每行的新 skills 与覆盖分类；**只读**，不改库。

    ``stats`` 由调用方传入并**原地累加**：中途抛异常时，汇总里仍然是"处理到第几条"
    的真实进度，而不是一组零（汇总在 ``finally`` 里，不能靠正常返回才准）。
    """
    stats["rows_total"] = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]

    changes = []
    has_sources = _has_sources_column(conn)
    select = "SELECT id, title, job_desc, skills" + (
        f", {SOURCES_COLUMN}" if has_sources else ""
    ) + " FROM job ORDER BY id"
    for row in conn.execute(select):
        job_id, title, desc, old_value = row[:4]
        old_sources = str(row[4] or "") if has_sources else ""
        if limit > 0 and stats["processed"] >= limit:
            break
        old_labels = _labels(old_value)
        new_value = extract_skills(title or "", desc or "")
        new_labels = _labels(new_value)
        new_sources = encode_skill_sources(new_labels, title)

        stats["processed"] += 1
        stats["labels_before"] += len(old_labels)
        stats["labels_after"] += len(new_labels)

        if old_sources != new_sources:
            stats["sources_changed"] += 1

        if tuple(old_labels) == tuple(new_labels) and old_sources == new_sources:
            stats["same"] += 1
            continue

        stats["changed"] += 1
        if len(new_labels) > len(old_labels):
            stats["more"] += 1
        elif len(new_labels) < len(old_labels):
            stats["fewer"] += 1
        else:
            stats["reordered"] += 1
        old_set, new_set = set(old_labels), set(new_labels)
        stats["added"] += len(new_set - old_set)
        stats["removed"] += len(old_set - new_set)
        changes.append((job_id, old_labels, new_labels, new_value, new_sources))
    return changes


def _apply_changes(conn: sqlite3.Connection, changes: list) -> int:
    """只写 ``skills`` 与 ``skills_sources`` 两列，单事务提交；返回实际影响行数。"""
    before = conn.total_changes
    conn.executemany(
        f"UPDATE job SET skills = ?, {SOURCES_COLUMN} = ? WHERE id = ?",
        [(new_value, sources, job_id)
         for job_id, _old, _new, new_value, sources in changes],
    )
    conn.commit()
    return conn.total_changes - before


def _print_summary(*, stats: dict, elapsed: float, dry_run: bool,
                   backup_path, written: int, fingerprint_ok) -> None:
    """汇总。**正常 / 异常 / 中断都必须走到这里**（调用方放在 finally）。"""
    mode = "dry-run（未写库）" if dry_run else "实跑"
    print("\n" + "=" * 64)
    print(f"skills 重算汇总 — {mode}")
    print("=" * 64)
    print(f"处理：{stats['processed']} 条（全库 {stats['rows_total']} 条）")
    print(
        f"变更：{stats['changed']} 条"
        f"（变多 {stats['more']} / 变少 {stats['fewer']} / 同数不同序 {stats['reordered']}"
        f" / 未变 {stats['same']}）"
    )
    print(
        f"标签总数：{stats['labels_before']} → {stats['labels_after']}"
        f"（{stats['labels_after'] - stats['labels_before']:+d}）"
        f"；新增 {stats['added']} 个 / 移除 {stats['removed']} 个"
    )
    print(
        f"来源标注：{stats['sources_changed']} 条新增/更新"
        "（title=标题直接写明 / desc=仅正文提及；编码 |title:X|desc:Y|）"
    )
    if not dry_run:
        print(f"写入：{written} 行（只改 skills 与 skills_sources 两列）")
        if fingerprint_ok is True:
            print("红线校验：其它列指纹前后一致（job_status / 薪资 / 标题 / 正文 / job_key 未变）")
        elif fingerprint_ok is False:
            print("红线校验：[RED LINE] 其它列指纹发生变化，见上方说明")
    print(f"耗时：{_fmt_duration(elapsed)}")
    if backup_path is not None:
        try:
            size = f"{backup_path.stat().st_size} 字节，"
        except OSError:  # 备份被人删掉也不该让汇总崩掉
            size = ""
        print(f"备份：{backup_path}（{size}integrity_check=ok）")
    elif dry_run:
        print("备份：未备份（dry-run 不写库）")
    else:
        print("备份：未生成（本次没有需要写入的变更）")
    print("=" * 64)


def run(args) -> int:
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    if not db_path.exists():
        logger.error("数据库不存在：%s", db_path)
        return 2

    started = time.time()
    stats = {
        "processed": 0, "changed": 0, "same": 0, "more": 0, "fewer": 0,
        "reordered": 0, "added": 0, "removed": 0, "sources_changed": 0,
        "labels_before": 0, "labels_after": 0, "rows_total": 0,
    }
    backup_path = None
    written = 0
    fingerprint_ok = None
    conn = None

    print(
        f"库：{db_path}（dry-run={args.dry_run}，limit={args.limit or '全库'}）"
    )
    try:
        conn = _connect(db_path)
        before_fingerprint = _fingerprint(conn)
        changes = _collect_changes(conn, args.limit, stats)
        print(
            f"待处理 {stats['processed']} 条，其中需要改写 {stats['changed']} 条；"
            f"标签 {stats['labels_before']} → {stats['labels_after']}"
            f"（{stats['labels_after'] - stats['labels_before']:+d}）"
        )
        for idx, (job_id, old_labels, new_labels, _new_value, _sources) in enumerate(changes, 1):
            print(
                f"[{idx}/{len(changes)}] id={job_id} "
                f"{len(old_labels)}→{len(new_labels)} 个标签：{','.join(new_labels)[:80]}"
            )

        if args.dry_run or not changes:
            return 0

        # 写前备份：拿不到完整备份就一条都不写（没有回滚锚点就没法负责）
        try:
            backup_path = _backup(db_path)
        except BackupError as exc:
            logger.error("%s", exc)
            print(f"\n[ABORT] {exc}\n        未做任何写入；坏备份已删除。")
            return 5
        print(f"已备份：{backup_path}（{backup_path.stat().st_size} 字节，integrity_check=ok）")

        # 老库副本可能还没有新列：在备份之后、写入之前补上（dry-run 不会走到这里）
        if _ensure_sources_column(conn):
            print(f"已补列：job.{SOURCES_COLUMN} VARCHAR(500)（旧库副本，schema_sync 同款 DDL）")

        written = _apply_changes(conn, changes)
        after_fingerprint = _fingerprint(conn)
        fingerprint_ok = before_fingerprint == after_fingerprint
        if written != len(changes):
            print(
                f"\n[WARN] 预期写 {len(changes)} 行，实际影响 {written} 行 —— "
                "有行在读取与写入之间被删除了，请核对。"
            )
        return 0 if fingerprint_ok else 3
    except sqlite3.OperationalError as exc:
        if conn is not None:
            conn.rollback()
        if _is_lock_error(exc):
            print(
                f"\n[ABORT] 数据库写锁等待超过 30 秒，本批中止（不原地重试）。"
                f"\n        已提交的写入都在库里，未提交的已回滚；备份：{backup_path}"
            )
            return 6
        logger.exception("skills 重算失败")
        print(f"\n[ABORT] SQLite 错误：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 批量层兜底，异常不逃逸出汇总
        if conn is not None:
            conn.rollback()
        logger.exception("skills 重算中途异常中止")
        print(f"\n[ABORT] 未预期的错误：{type(exc).__name__}: {exc}")
        return 1
    finally:
        _print_summary(
            stats=stats, elapsed=time.time() - started, dry_run=args.dry_run,
            backup_path=backup_path, written=written, fingerprint_ok=fingerprint_ok,
        )
        if fingerprint_ok is False:
            print(
                "[RED LINE] 其它列在本次运行期间被改动（不是本脚本的 UPDATE 干的）。\n"
                f"        备份（回滚锚点）：{backup_path}\n"
                "        先人工核对是谁在并发写这个库，不要直接重跑。"
            )
        if conn is not None:
            conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="按 title + job_desc 重算 skills 列（只改这一列，覆盖旧值）"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库（也不备份）")
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="只处理前 N 条（调试用；0 = 全库，默认 0）",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """最外层异常边界：任何退出都有可读的收尾，而不是一屏堆栈。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n已中断：汇总见上（Ctrl-C 也会走 finally 打印）。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
