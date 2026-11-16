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
2. **写库前先备份，且备份必须是完整的**：``data/recsys.db`` →
   ``data/recsys.db.bak-<时间戳>``，用 SQLite 在线备份接口做（不是文件拷贝），
   建完立刻 ``PRAGMA integrity_check``，不通过就中止（详见 :func:`_backup`）。
   ``--dry-run`` 不备份也不写（没有写入就没有可回滚的东西）。
3. **可续跑**：成功的岗位号记进 ``data/backfill_detail_state.json``；
   下次带 ``--resume`` 就从上一批之后继续，不会把已经补好的再抓一遍。
   状态文件**原子写入**；读到半截 JSON 时**不会**当成空文件（那会把已有记录
   永久覆盖），而是中止并要求人工介入（详见 :func:`_load_state`）。

**异常边界与退出码（2026-11-17 补）**

本脚本一跑就是几十分钟，**汇总绝不能因为中途抛异常而丢掉**：

- 单条失败由 :class:`DetailFetcher` 自己分类返回，不抛异常；
- 批量层（写库 / 提交）接 ``sqlite3`` 与其余异常，接住后**先打印汇总**
  （成功 / 失败 / 分类 / 覆盖率），再按类别给出退出码与后续动作；
- 连接层：脚本自己的 ``sqlite3`` 连接是**另一条路径**（不经过 SQLAlchemy），
  所以显式设 ``busy_timeout=30s`` 与 ORM 对齐（``app/database.py`` 的
  ``SQLITE_BUSY_TIMEOUT_MS``）。等满 30 秒仍拿不到写锁 = **明确失败、不原地重试**
  （再试一次只会再等 30 秒），状态文件停在上一条已提交的记录上，可直接 ``--resume``。

| 退出码 | 含义 | 能否直接 ``--resume`` |
|---|---|---|
| 0 | 正常跑完 | 能 |
| 2 | 库不存在 | 修正路径后重跑 |
| 3 | ``job_status`` 红线被碰（写入期间被改动） | **不能**，先人工核对（备份已保留） |
| 4 | 连续被拦（WAF / 登录墙），主动停批 | 能（换网络 / 等一段时间） |
| 5 | 备份建不出来或 ``integrity_check`` 不通过 | **不能**，库里没有本次写入 |
| 6 | 写锁等待超时（30 秒） | 能（上一条已提交，状态一致） |
| 7 | 状态文件损坏 / 不属于当前库 | **不能**，人工处理后重跑 |
| 130 | Ctrl-C 中断 | 能（已提交的记录都记进状态文件了） |

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

**失败分类与止损（2026-11-12 补）**

只报「成功 / 失败」没法决策 —— 跑 555 条时真正要回答的是「该停、该重试、还是该放弃」。
所以失败按 ``FAILURE_KINDS`` 分成 WAF/风控 · 登录墙 · 网络错误 · 解析失败 · 无该字段 · 其他，
并加两条止损：

1. **连续 ``--max-consecutive-blocked`` 条（默认 5）被拦截 → 立即停本批**（退出码 4）。
   判定「被拦」= WAF/风控 **或** 登录墙 —— 2026-11-12 实测：51job 的软限流
   表现为「302 到 login.51job.com」，只盯 WAF 的话这条止损永远不会触发。
2. **每 ``--batch-size`` 条（默认 50）冷却 ``--cooldown`` 秒（默认 30）**。
   域名闸门本身是 20 秒/请求，一批 50 条已经连打十几分钟；30 秒停顿让对方的
   频率统计窗口滑过去，相对整批耗时可忽略。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK：往 print 里塞一个 GBK 编不了的字符（emoji 之类）会抛
# UnicodeEncodeError。这个脚本一跑就是几十分钟，崩在收尾处等于把整批汇总丢掉
# （2026-11-12 实测踩到：止损分支里放了个 emoji，让成功/失败统计一行都没打印出来）。
# 这里把编码错误降级为替换，并在源码里禁用非 GBK 字符（有测试锁着）。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 非标准流（被重定向）时忽略
    pass

from app.crawlers.detail_fetcher import (  # noqa: E402
    BLOCKED_KINDS,
    FAILURE_KINDS,
    KIND_OTHER,
    KIND_WAF,
    MIN_DESC_LEN,
    DetailFetcher,
)

logger = logging.getLogger("backfill_detail")

DEFAULT_DB = ROOT / "data" / "recsys.db"
DEFAULT_STATE = ROOT / "data" / "backfill_detail_state.json"

# 等锁预算：与 ``app/database.py`` 的 ``SQLITE_BUSY_TIMEOUT_MS = 30000`` 对齐。
# 脚本自己的 ``sqlite3`` 连接不经过 SQLAlchemy，**不会**继承 ORM 的 30 秒；
# Python 默认 ``timeout=5`` 秒 —— 全库回填撞上应用的写事务时，5 秒后直接抛栈。
BUSY_TIMEOUT_SECONDS = 30.0


class BackupError(RuntimeError):
    """备份建不出来或校验不通过。调用方必须中止，不许拿坏备份继续写。"""


class StateFileError(RuntimeError):
    """状态文件不可用（损坏 / 属于别的库）。必须人工介入，不许当空文件覆盖。"""


# 需要回填的判定：**只按正文长度**（没有正文，或正文短得明显不是正文）。
#
# 原先还要求 ``detail_html`` 非空，结果是「永远填不完」：
# 2026-11-12 实测，唯一能拿到数据的路径是搜索接口（``source=search_api``），
# 它返回的是一条 JSON 记录、**没有整页 HTML**，所以成功回填 52 条之后
# ``detail_html`` 仍是 0/555 —— 每一行都还满足「待回填」，
# 全量预估永远停在 555 条，脚本会把已经补好的行反复重抓。
#
# ``detail_html`` 的缺口是真实存在的（详情页未登录态打不开），但它属于
# **另一条取数路径**的问题，不该混进「这条岗位还差正文」的判据里。
# 每次运行结束时会把 detail_html 覆盖率单独打印出来，不藏。
_NEED_BACKFILL = (
    "(url IS NOT NULL AND TRIM(url) <> '') AND ("
    " job_desc IS NULL OR LENGTH(TRIM(job_desc)) < ?"
    ")"
)


def _connect(db_path: Path) -> sqlite3.Connection:
    # ``timeout=`` 就是 sqlite3 的 busy_timeout；显式再 PRAGMA 一次，
    # 是为了让「这个连接会等多久」在读代码时一眼可见（默认只有 5 秒）。
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
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
    """用 SQLite 在线备份接口做整库快照，并当场校验完整性。

    **为什么不是 ``shutil.copy2``**：文件拷贝按字节复制，完全不理会写入方正
    在改哪一页 —— 恰好拷到「日志写了、主库页还没落」的瞬间，副本就是撕裂的。
    实测边写边拷，**0.28%~0.54%** 的副本 ``integrity_check`` 不通过，而旧的
    脚本对副本不校验、也永不清理：跑全库回填一旦失败，拿到的"备份"本身可能是坏的。

    ``Connection.backup()`` 走 SQLite 的在线备份 API：它在一条读事务里按页复制，
    源库在复制期间被改过**就从第一页重来**，全部拷完才在目标库一次性提交。
    因此副本一定是**某个时刻的一致快照**，不需要停写、也不依赖拷贝时机。

    **生命周期（本文件唯一规则，别在别处另定）**：

    - 校验通过 → **保留**。它是本批写入的回滚锚点，成功和失败都保留（失败时更需要它）；
    - ``integrity_check`` 不通过 / 复制中途抛异常 → **立刻删除**半截文件并抛
      :class:`BackupError`。留着一个坏副本比没有更危险：它会让人以为"还能回滚"。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    # 同一秒内重复调用（测试 / 脚本连环跑）时加序号，绝不覆盖已有备份
    seq = 2
    while target.exists():
        target = db_path.with_name(f"{db_path.name}.bak-{stamp}-{seq}")
        seq += 1

    source = dest = None
    ok = False
    try:
        source = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS)
        dest = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_SECONDS)
        source.backup(dest)
        verdict = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            raise BackupError(f"备份 integrity_check 不通过：{verdict}")
        ok = True
    except sqlite3.Error as exc:
        # 连不上源库 / 目标不可写 / 复制被锁住 —— 统统是"这次没有备份"
        raise BackupError(f"备份失败：{exc}") from exc
    finally:
        if source is not None:
            source.close()
        if dest is not None:
            dest.close()
        if not ok:
            # 半截 / 校验不过的副本一律删掉，不给"有备份"的错觉
            target.unlink(missing_ok=True)
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


def _load_state(path: Path, db_path: Path | None = None) -> dict:
    """读状态文件；**损坏时抛 :class:`StateFileError`，不当空文件**。

    2026-11-17 之前是 ``except (ValueError, OSError): return {"ok": [], "failed": {}}``。
    后果是：一次写到一半的 JSON（Ctrl-C / 断电 / 磁盘满）会让**所有历史记录
    静默消失**，紧接着下一次保存把它们永久覆盖 —— 于是 `--resume` 把已经补好的
    几百条全部重抓一遍，而且没人知道记录是怎么没的。

    现在的规则：

    - 文件不存在 → 新状态（这是唯一合法的"空"）；
    - 文件存在但解析不了 / 不是对象 → 抛错，原文件**一字不动**，交人工处理；
    - 文件里记了 ``db`` 且与本次要跑的库不同 → 抛错。别的库的 ``ok`` 列表
      拿来跳过本库的岗位，等于凭空认定"这条已经补过了"。
    """
    if not path.exists():
        return {"ok": [], "failed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise StateFileError(
            f"状态文件读不出来（{path}）：{exc}。"
            "它可能只写了一半 —— 先人工检查/修复或改名备份，不要删了直接跑。"
        ) from exc
    if not isinstance(data, dict):
        raise StateFileError(f"状态文件顶层不是对象（{path}），拒绝覆盖。")
    recorded_db = data.get("db")
    if db_path is not None and recorded_db and recorded_db != str(db_path):
        raise StateFileError(
            f"状态文件属于另一个库：记录 {recorded_db}，本次 {db_path}。"
            "换库请用 --state 指定对应的状态文件。"
        )
    data.setdefault("ok", [])
    data.setdefault("failed", {})
    return data


def _save_state(path: Path, state: dict) -> None:
    """原子写：先写同目录临时文件再 ``os.replace``。

    直接 ``write_text`` 被中断会留下半截 JSON，正是上面 :func:`_load_state`
    要防的东西。``os.replace`` 在同一分区上是原子的 —— 读到的要么是旧版本、
    要么是新版本，没有中间态。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _verify_one_status(conn: sqlite3.Connection, job_id: int, expected) -> bool:
    """提交**之前**核对这一行的 ``job_status``。

    红线校验原来的位置在整批跑完之后 —— 那时写入早已逐条提交，发现被改也
    回滚不掉了，只能干瞪眼。挪到每条 ``commit`` 之前：事务还没提交，
    ``rollback`` 能把本条写入一起撤掉。
    """
    row = conn.execute("SELECT job_status FROM job WHERE id = ?", (job_id,)).fetchone()
    actual = row["job_status"] if row else None
    if actual != expected:
        logger.error(
            "job_status 被改动！id=%s 回填前=%r 写入期间=%r（本条写入已回滚）",
            job_id, expected, actual,
        )
        return False
    return True


def _verify_status_unchanged(conn: sqlite3.Connection, before: dict) -> bool:
    after = _status_snapshot(conn, list(before))
    diff = {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)}
    if diff:
        logger.error("job_status 被改动！这是红线：%s", diff)
        return False
    return True


def _fmt_duration(seconds: float) -> str:
    """把秒数说成人话（预估耗时要能直接读给用户听）。"""
    if seconds <= 0:
        return "0 秒"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {sec} 秒"
    return f"{sec} 秒"


def _kind_label(kind: str) -> str:
    return FAILURE_KINDS.get(kind, kind or "未分类")


def _is_lock_error(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _print_batch_summary(
    *, total: int, ok: int, failed: int, skipped: int, counts: dict,
    processed: int, elapsed: float, dry_run: bool, backlog: int,
) -> None:
    """本批汇总。**正常结束和异常中止都必须调用它**。

    跑几十分钟的批次，只在成功路径打印统计，等于一出事就什么都不知道 ——
    2026-11-17 之前写锁一抛栈，成功/失败/分类/预估一行都没输出。
    """
    avg = elapsed / processed if processed else 0.0
    print(
        f"\n完成：成功 {ok} / 失败 {failed} / 跳过 {skipped}"
        f"（本批处理 {processed}/{total} 条，用时 {_fmt_duration(elapsed)}）"
        + ("（dry-run，未写库）" if dry_run else "")
    )
    if failed:
        print("失败分类：")
        for kind, label in FAILURE_KINDS.items():
            n = counts.get(kind, 0)
            if n:
                print(f"  {label:<10} {n:>4} 条  ({n / failed * 100:.1f}%)")
    if avg:
        print(f"单条平均耗时 {avg:.1f} 秒（含节流等待）")
        remaining = max(0, backlog - ok)
        if remaining:
            print(
                f"按本次速度估算：剩余 {remaining} 条约需 "
                f"{_fmt_duration(avg * remaining)}（不含冷却与失败重试）"
            )


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
    try:
        state = _load_state(state_path, db_path)
    except StateFileError as exc:
        logger.error("%s", exc)
        print(f"\n[ABORT] 状态文件不可用，未连接数据库、未建备份、未做任何写入。\n        {exc}")
        return 7
    # 记下所属库：下次换库跑同一个状态文件时会被 _load_state 拒绝
    state["db"] = str(db_path)
    skip_ids = {int(x) for x in state.get("ok", [])} if args.resume else set()

    conn = _connect(db_path)
    try:
        # 先取全量待回填（用于预估），再按 --limit 截断本批
        all_rows = _pending_rows(conn, 0, skip_ids)

        # --resume 的语义是「继续往前走」，不是「把上次失败的原地重试一遍」。
        # 失败过的（尤其被登录墙挡住的）在同一次运行里重试几乎必然再失败，
        # 却会占满 --limit 名额、让新数据一条都推不动。所以把它们排到最后：
        # 有余额才重试，没余额就先保证覆盖面。
        failed_ids = set()
        if args.resume:
            failed_ids = {int(k) for k in state.get("failed", {}) if str(k).isdigit()}
        if failed_ids:
            all_rows.sort(key=lambda r: (r["id"] in failed_ids, r["id"]))
        rows = all_rows[: args.limit] if args.limit > 0 else all_rows
        if not rows:
            print(f"没有待回填的岗位（库里已无缺失项，或 --limit 太小）。库：{db_path}")
            return 0

        print(
            f"待回填 {len(rows)} 条（全库剩余 {len(all_rows)} 条；库：{db_path}，"
            f"dry-run={args.dry_run}，resume={args.resume}）"
        )

        backup = None
        if not args.dry_run:
            try:
                backup = _backup(db_path)
            except BackupError as exc:
                # 备份拿不到 / 不完整时**一条都不写**：没有回滚锚点就没法负责
                logger.error("%s", exc)
                print(f"\n[ABORT] {exc}\n        未做任何写入；坏备份已删除。")
                return 5
            print(f"已备份：{backup}（{backup.stat().st_size} 字节，integrity_check=ok）")

        ids = [r["id"] for r in rows]
        status_before = _status_snapshot(conn, ids)

        fetcher = DetailFetcher(min_interval=args.interval)
        ok = failed = skipped = 0
        counts = {kind: 0 for kind in FAILURE_KINDS}
        consecutive_waf = 0
        stopped_by_waf = False
        red_line_broken = False
        aborted_by = ""      # 非空 = 批量层接到的异常（写锁 / 其他），不影响汇总输出
        abort_code = 0
        processed = 0
        started = time.time()

        # ---- 批量层异常边界 ----
        # 写锁、磁盘满、状态文件写失败都在这里落地：先回滚本条未提交的写入，
        # 记下原因，**汇总照常打印**（在 finally 里），再把原因带出委托方。
        try:
            for idx, row in enumerate(rows, 1):
                processed = idx
                result = await fetcher.fetch(
                    row["url"], keyword=row["title"] or "", city=row["city"] or ""
                )
                if result.ok:
                    consecutive_waf = 0
                    if args.dry_run:
                        ok += 1
                    else:
                        _write_one(conn, row["id"], result)
                        # 红线校验挪到 commit **之前**：事务尚未提交时发现
                        # job_status 被动过，rollback 能连同本条写入一起撤销。
                        if not _verify_one_status(
                            conn, row["id"], status_before.get(row["id"])
                        ):
                            conn.rollback()
                            red_line_broken = True
                            break
                        conn.commit()
                        ok += 1
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
                    consecutive_waf = 0
                    print(f"[{idx}/{len(rows)}] skip id={row['id']} 原因={result.reason}")
                else:
                    failed += 1
                    kind = result.failure or KIND_OTHER
                    counts[kind] = counts.get(kind, 0) + 1
                    consecutive_waf = consecutive_waf + 1 if kind in BLOCKED_KINDS else 0
                    if not args.dry_run:
                        # 状态文件同时存类别与原文：类别用来聚合，原文用来排查
                        state.setdefault("failed", {})[str(row["id"])] = {
                            "kind": kind,
                            "reason": result.reason,
                        }
                        _save_state(state_path, state)
                    print(
                        f"[{idx}/{len(rows)}] fail id={row['id']} "
                        f"类别={_kind_label(kind)} 原因={result.reason}"
                    )

                # ---- 止损一：连续 N 条被「拦住」（WAF / 登录墙） ----
                # 单条可能只是抽风；连续 N 条说明对方已经把我们这条 IP 标了。
                # 此时继续硬顶只会把临时限流升级为封 IP，而且成功率会掉到 0。
                # 实测依据见 FAILURE_KINDS 下方注释：51job 的软限流表现为「302 到登录页」，
                # 只盯 WAF 的话这条止损永远不会触发。
                if consecutive_waf >= args.max_consecutive_blocked:
                    stopped_by_waf = True
                    print(
                        f"\n[STOP] 连续 {consecutive_waf} 条被站点拦截"
                        f"（WAF 或登录墙）—— 立即停止本批。\n"
                        f"       剩余 {len(rows) - idx} 条未处理；"
                        f"换网络/等一段时间后再用 --resume 继续。"
                    )
                    break

                if idx % 10 == 0:
                    elapsed = time.time() - started
                    speed = elapsed / idx
                    print(
                        f"---- 进度 {idx}/{len(rows)}：成功 {ok} / 失败 {failed} / 跳过 {skipped}"
                        f"（{speed:.1f} 秒/条，已用 {_fmt_duration(elapsed)}）"
                    )

                # ---- 止损二：批次之间冷却 ----
                # 默认 50 条一批、批间 30 秒。依据：域名闸门本身是 20 秒/请求，
                # 一批 50 条已经连续打了十几分钟；30 秒的停顿是为了让对方的
                # 频率统计窗口滑过去，成本几乎为零（相对整批耗时）。
                if (
                    args.batch_size > 0
                    and idx % args.batch_size == 0
                    and idx < len(rows)
                    and args.cooldown > 0
                ):
                    print(f"---- 批次结束（{idx}/{len(rows)}），冷却 {args.cooldown:g} 秒后再继续…")
                    await asyncio.sleep(args.cooldown)
        except sqlite3.OperationalError as exc:
            conn.rollback()
            lock = _is_lock_error(exc)
            aborted_by = (
                f"数据库写锁等待超过 {BUSY_TIMEOUT_SECONDS:g} 秒，本批中止（不原地重试）"
                if lock else f"SQLite 错误：{exc}"
            )
            abort_code = 6 if lock else 1
            logger.error("%s", aborted_by)
        except Exception as exc:  # noqa: BLE001 - 批量层兜底，异常不逃逸出汇总
            conn.rollback()
            aborted_by = f"未预期的错误：{type(exc).__name__}: {exc}"
            abort_code = 1
            logger.exception("回填中途异常中止")
        finally:
            try:
                await fetcher.aclose()
            finally:
                # 汇总放在 finally：正常 / 写锁 / 红线 / Ctrl-C 都会走到这里
                _print_batch_summary(
                    total=len(rows), ok=ok, failed=failed, skipped=skipped,
                    counts=counts, processed=processed,
                    elapsed=time.time() - started, dry_run=args.dry_run,
                    backlog=len(all_rows),
                )

        # detail_html 单独报一次：它只有 html 路径才会产生，而当前唯一可用的
        # 取数路径是搜索接口（拿不到整页）。把缺口摆出来，别让它被"成功 52 条"盖住。
        # 读操作也不许逃逸：查不出来就明说，不能把汇总之后的路整段吞掉。
        try:
            total_jobs = conn.execute("select count(*) from job").fetchone()[0]
            html_rows = conn.execute(
                "select count(*) from job where detail_html is not null and trim(detail_html) <> ''"
            ).fetchone()[0]
            print(
                f"detail_html 覆盖：{html_rows}/{total_jobs}"
                f"（唯一可用路径是搜索接口，不返回整页 HTML —— 已知缺口，见报告）"
            )
        except sqlite3.Error as exc:
            print(f"detail_html 覆盖率查询失败：{exc}")

        if red_line_broken:
            print(
                f"\n[RED LINE] job_status 在写入期间被改动，已停批；本条写入已回滚。\n"
                f"        本批已提交 {ok} 条（其状态在提交前都核对过）。\n"
                f"        备份（回滚锚点）：{backup}\n"
                f"        该红线说明有别的东西在改 job_status —— 先人工核对，"
                f"不要直接 --resume。"
            )
            return 3
        if aborted_by:
            print(
                f"\n[ABORT] {aborted_by}\n"
                f"        已提交 {ok} 条（库里没有未提交的半截写入）。\n"
                f"        状态文件按行原子写：崩溃点那一行可能已提交但未记录，"
                f"重跑会重新抓一次（重复写入是幂等的）。\n"
                + (f"        备份保留：{backup}\n" if backup else "")
                + ("        可直接带 --resume 重跑。" if abort_code == 6 else
                   "        先排查原因再重跑。")
            )
            return abort_code
        if not _verify_status_unchanged(conn, status_before):
            print(
                f"\n[RED LINE] 本批已提交 {ok} 条，之后 job_status 又发生了变化 —— "
                f"这些提交无法自动回滚。\n"
                + (f"        备份（回滚锚点）：{backup}\n" if backup else "")
                + "        请人工核对；不要直接 --resume。"
            )
            return 3
        print("job_status 校验：所有被回填岗位的状态与回填前一致。")
        return 4 if stopped_by_waf else 0
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
    parser.add_argument(
        "--max-consecutive-blocked", type=int, default=5, metavar="N",
        help="连续 N 条被站点拦截（WAF 或登录墙）就停止本批（默认 5）；退出码 4 表示因此停止",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, metavar="N",
        help="每 N 条为一批，批间冷却（默认 50；0 = 不冷却）",
    )
    parser.add_argument(
        "--cooldown", type=float, default=30.0, metavar="SEC",
        help="批次之间的冷却秒数（默认 30）；依据见 --help 顶部说明",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    """最外层异常边界：保证任何退出都有可读的收尾说明，而不是一屏堆栈。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print(
            "\n已中断：汇总见上（Ctrl-C 也会走 finally 打印）；"
            "成功写入的记录已提交，再跑时加 --resume 继续。"
        )
        return 130
    except sqlite3.Error as exc:
        lock = _is_lock_error(exc)
        print(
            f"\n[ABORT] {'数据库写锁等待超时' if lock else 'SQLite 错误'}：{exc}\n"
            "        已提交的写入都在库里，未提交的没有落盘；"
            "备份与状态文件路径见上方输出。"
        )
        return 6 if lock else 1
    except Exception as exc:  # noqa: BLE001 - 最后一层兜底，不让堆栈盖掉已有输出
        logger.exception("回填脚本异常退出")
        print(f"\n[ABORT] 未预期的错误：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
