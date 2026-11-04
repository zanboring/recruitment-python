"""并发写压力脚本：复现并度量 SQLite 写锁冲突（对应缺陷 D-6）。

**要复现的是什么**
`log_action` 用**独立会话**写 `sys_log`，与业务写事务抢同一把 SQLite 写锁。
SQLite 的 `busy_timeout` 默认是 0 —— 拿不到锁就**立刻**抛
`sqlite3.OperationalError: database is locked`，在接口上表现为偶发 500
（重试一次恢复）。这正是演示时偶发 500 的成因。

**为什么用脚本而不是接口压测**
接口压测会把「锁冲突」「鉴权」「业务校验」混在一起，数字不可解释。
这里直接复刻两条写路径（业务写 + 独立会话的日志写），让数字只反映锁争用。

**关键：脚本用的是 app 自己的 engine**（`app.database.engine`），
所以改引擎配置前后跑同一份脚本，数字的变化就是修复效果 —— 而不是
「另一个配置下的另一次实验」。

用法：
    python scripts/stress_sqlite_writes.py --db data/stress.db --concurrency 10 --rounds 20
    python scripts/stress_sqlite_writes.py --json

参数：
    --db           SQLite 文件路径（默认 data/stress.db，运行前会重建）
    --concurrency  并发操作数（默认 10）
    --rounds       每个并发任务串行做多少轮（默认 20）
    --hold-ms      业务事务持锁时长（毫秒，默认 5）—— 模拟一个请求里的多次查询
    --json         以 JSON 输出
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，打印「⇒」这类符号会直接 UnicodeEncodeError 崩掉脚本。
# 压测脚本崩了就没有数字，所以先把 stdout 的编码错误降级为替换而不是抛异常。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - 非标准流（被重定向到别的编码）时忽略
    pass


def _preparse_db() -> str:
    """在 import app.* 之前取到 --db。

    `Settings()` 在**导入时**读环境变量，所以数据库路径必须在导入 app 包之前
    设进环境变量 —— 脚本自己解析一遍 argv 就是为了满足这一点。
    """
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--db" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--db="):
            return arg.split("=", 1)[1]
    return "data/stress.db"


_DB_PATH = _preparse_db()
os.environ["DB_TYPE"] = "sqlite"
os.environ["DB_NAME"] = _DB_PATH

from sqlalchemy import text  # noqa: E402

from app.database import Base, async_session, engine  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.models.sys_log import SysLog  # noqa: E402


def _is_lock_error(exc: BaseException) -> bool:
    """判断是不是 SQLite 写锁冲突（接口上就是 500）。"""
    text_repr = f"{type(exc).__name__}: {exc}".lower()
    return "database is locked" in text_repr or "database table is locked" in text_repr


async def _one_operation(index: int, round_no: int, hold_ms: float) -> tuple:
    """一次「业务写 + 日志写」，复刻 log_action 与业务事务抢锁的场景。

    返回 (类别, 错误信息, 本次耗时毫秒)
    类别："ok" / "locked" / "other_error"

    **耗时必须一起记**：SQLite 的 busy handler 是「等锁」机制，
    失败有两种完全不同的形态 ——
      · 等满了 busy_timeout 才放弃（耗时 ≈ busy_timeout，说明旋钮调小了）；
      · 立刻返回 SQLITE_BUSY（耗时 ≈ 0，说明撞上了 SQLite 的死锁保护，
        这时调大 busy_timeout 一点用都没有）。
    只看「失败了几次」分不出这两种，修复方向却完全相反。
    """
    started = time.perf_counter()

    def _elapsed_ms() -> float:
        return round((time.perf_counter() - started) * 1000, 1)

    # ---- 业务写：自己的会话，事务里先 flush（拿到写锁），再 commit ----
    try:
        async with async_session() as db:
            db.add(Job(
                title=f"压力测试岗位-{index}-{round_no}",
                company_name="压测公司",
                source_site="seed",
                job_key=f"stress_{index}_{round_no}",
                city="长沙",
                job_status="NEW",
            ))
            await db.flush()
            # 持锁一小会儿：真实请求在 commit 前还要查库/写库，
            # 这段时间里另一个写事务就会撞上同一把锁。
            await asyncio.sleep(hold_ms / 1000)
            await db.commit()
    except Exception as e:  # noqa: BLE001 - 压测脚本要把错误分类统计
        return ("locked" if _is_lock_error(e) else "other_error"), f"{type(e).__name__}: {e}", _elapsed_ms()

    # ---- 日志写：独立会话（与 app/utils/log_decorator.py 同一模式）----
    try:
        async with async_session() as log_db:
            log_db.add(SysLog(
                username="stress",
                action="压力测试",
                method="scripts.stress_sqlite_writes",
                uri="/stress",
                ip="127.0.0.1",
                params="{}",
                success=1,
            ))
            await log_db.commit()
    except Exception as e:  # noqa: BLE001
        return ("locked" if _is_lock_error(e) else "other_error"), f"{type(e).__name__}: {e}", _elapsed_ms()

    return "ok", "", _elapsed_ms()


async def _pragmas() -> dict:
    """读回当前生效的 SQLite 配置 —— 修复前后的差异要看得见。"""
    async with engine.connect() as conn:
        journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    return {"journal_mode": journal, "busy_timeout": busy}


async def _prepare(db_path: str) -> None:
    """重建库文件并建表（每次跑都是同样的起点，数字才可比较）。"""
    p = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(p) + suffix)
        if target.exists():
            target.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def run(args) -> dict:
    await _prepare(args.db)
    pragmas = await _pragmas()

    started = time.perf_counter()
    results = await asyncio.gather(*[
        _one_operation(i, r, args.hold_ms)
        for i in range(args.concurrency)
        for r in range(args.rounds)
    ])
    elapsed = time.perf_counter() - started

    locked = [(msg, ms) for kind, msg, ms in results if kind == "locked"]
    other = [(msg, ms) for kind, msg, ms in results if kind == "other_error"]
    ok_ms = [ms for kind, _, ms in results if kind == "ok"]
    total = len(results)

    def _stat(values: list) -> dict:
        if not values:
            return {}
        ordered = sorted(values)
        return {
            "min_ms": ordered[0],
            "median_ms": ordered[len(ordered) // 2],
            "max_ms": ordered[-1],
        }

    return {
        "db": args.db,
        "concurrency": args.concurrency,
        "rounds": args.rounds,
        "hold_ms": args.hold_ms,
        "total_ops": total,
        "locked_errors": len(locked),
        "other_errors": len(other),
        "elapsed_sec": round(elapsed, 3),
        "ops_per_sec": round(total / elapsed, 1) if elapsed else 0,
        "sqlite": pragmas,
        "pool": type(engine.pool).__name__,
        # 失败操作的耗时分布：≈ busy_timeout 说明「等满了才放弃」；
        # ≈ 0 说明「立刻被拒」，两者的修复方向不同。
        "locked_duration": _stat([ms for _, ms in locked]),
        "ok_duration": _stat(ok_ms),
        "sample_errors": [msg for msg, _ in (locked + other)][:5],
    }


def _render(r: dict) -> str:
    lines = [
        "并发写压力测试结果：",
        f"  并发数={r['concurrency']}  轮数={r['rounds']}  持锁={r['hold_ms']}ms"
        f"  连接池={r['pool']}",
        f"  SQLite 配置：journal_mode={r['sqlite']['journal_mode']}"
        f"  busy_timeout={r['sqlite']['busy_timeout']}",
        f"  总操作数={r['total_ops']}  锁冲突失败={r['locked_errors']}"
        f"  其它错误={r['other_errors']}",
        f"  耗时={r['elapsed_sec']}s  吞吐={r['ops_per_sec']} ops/s",
    ]
    if r.get("locked_duration"):
        d = r["locked_duration"]
        lines.append(
            f"  失败操作耗时：min={d['min_ms']}ms 中位={d['median_ms']}ms max={d['max_ms']}ms"
            "（≈busy_timeout 说明等满了才放弃；≈0 说明立刻被拒）"
        )
    if r.get("ok_duration"):
        d = r["ok_duration"]
        lines.append(f"  成功操作耗时：中位={d['median_ms']}ms max={d['max_ms']}ms")
    if r["sample_errors"]:
        lines.append("  错误样例：")
        for msg in r["sample_errors"]:
            lines.append(f"    - {msg}")
    if r["locked_errors"]:
        lines.append(
            f"  => 结论：{r['locked_errors']}/{r['total_ops']} 次写操作因锁冲突失败"
            "（接口上表现为 500 database is locked）"
        )
    else:
        lines.append(f"  => 结论：{r['total_ops']} 次写操作全部成功，无锁冲突")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite 并发写压力测试（复现 D-6 写锁 500）")
    parser.add_argument("--db", default="data/stress.db", help="SQLite 文件路径（运行前会重建）")
    parser.add_argument("--concurrency", type=int, default=10, help="并发操作数")
    parser.add_argument("--rounds", type=int, default=20, help="每个并发任务的轮数")
    parser.add_argument("--hold-ms", type=float, default=5, help="业务事务持锁时长（毫秒）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    try:
        result = await run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else _render(result))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
