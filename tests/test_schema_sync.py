"""数据库结构自检（schema 与模型一致性）的回归测试。

**背景：这是一个真实缺陷的回归锁。**

``Base.metadata.create_all`` 只创建不存在的表，**不会给已存在的表加列**。
于是「给模型加一列」在**已有数据库**上静默不生效，而失败时机极晚极隐蔽：

1. 启动日志完全正常（create_all 无报错、默认管理员创建成功）；
2. 直到有人访问该表的接口，才抛 ``Unknown column`` / ``no such column`` → HTTP 500；
3. **影响面是该表的全部查询，而不是"用到新列的那一个"** ——
   因为 SQLAlchemy 的 SELECT 会列出模型里的所有列。

实测复现（给 ``job`` 加 ``last_checked_at`` 后，在缺该列的库上）：
``select(Job)`` → ``no such column: job.last_checked_at``
→ 岗位列表、统计图表、AI 工具查询同时挂掉。

此前这个风险靠 ``README`` 的「表结构变更（升级须知）」手工补列清单兜底，
但**清单已经漏项**：``job.last_checked_at`` 从未被写进那份清单。
文档兜底不可靠，所以改为启动时自动补齐（``app/schema_sync.py``）。

本文件锁住三件事：
- 缺失的列**会被自动补上**，且补齐后查询恢复正常（缺陷的验收条件）
- 不该自动处理的列（主键 / 唯一 / NOT NULL 无默认值）**不被乱动**，而是明确报告
- 同步过程**只增列**，不删列、不改类型
"""
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  导入以把全部表注册进 Base.metadata
from app.database import Base
from app.models.job import Job
from app.schema_sync import _can_auto_add, sync_missing_columns

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 选一个 nullable、无默认值的列来代表「后加的列」——与真实的
# job.last_checked_at 形态一致（旧行取 NULL 语义正确）。
LEGACY_DROPPED_COLUMN = "last_checked_at"


async def _make_legacy_database(tmp_path, drop_column: str = LEGACY_DROPPED_COLUMN):
    """造一个「表存在但缺列」的数据库，模拟升级前的旧库。

    做法：先用当前模型建全表，再删掉某一列 —— 这正是旧库与当前代码的差异形态。
    """
    dbfile = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    # 用原生 sqlite3 改表（模拟"这个库是旧代码建的"）
    con = sqlite3.connect(str(dbfile))
    con.execute(f"ALTER TABLE job DROP COLUMN {drop_column}")
    con.commit()
    con.close()
    return dbfile


def _columns_of(dbfile, table: str):
    con = sqlite3.connect(str(dbfile))
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 一、缺陷的验收条件：缺列会被补上，且补齐后查询恢复
# ---------------------------------------------------------------------------


async def test_旧库缺列会被自动补齐(tmp_path):
    """启动自检必须补上「模型有、数据库没有」的列。"""
    dbfile = await _make_legacy_database(tmp_path)
    assert LEGACY_DROPPED_COLUMN not in _columns_of(dbfile, "job"), "前置条件：旧库确实缺这一列"

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.begin() as conn:
        result = await sync_missing_columns(conn)
    await engine.dispose()

    assert ("job", LEGACY_DROPPED_COLUMN) in result.added, (
        f"缺列未被补齐。added={result.added} skipped={result.skipped}"
    )
    assert LEGACY_DROPPED_COLUMN in _columns_of(dbfile, "job"), "补列后数据库里应能看到该列"


async def test_补齐后岗位查询恢复正常(tmp_path):
    """**这是缺陷真正的验收条件** —— 补齐前 select(Job) 会报「未知列」。"""
    dbfile = await _make_legacy_database(tmp_path)

    # 前置：不加处理时，查询确实是坏的（证明这个测试测的是真实问题）
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.connect() as conn:
        with pytest.raises(OperationalError) as excinfo:
            await conn.execute(select(Job).limit(1))
        assert "last_checked_at" in str(excinfo.value), (
            "预期报「缺列」，否则本测试的前提不成立"
        )

    # 走一次自检
    async with engine.begin() as conn:
        await sync_missing_columns(conn)

    # 修复后必须能正常查询
    async with engine.connect() as conn:
        await conn.execute(select(Job).limit(1))
    await engine.dispose()


async def test_启动自检是幂等的(tmp_path):
    """已经一致的库上不应重复加列（否则第二次启动会失败）。"""
    dbfile = await _make_legacy_database(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")

    async with engine.begin() as conn:
        first = await sync_missing_columns(conn)
    assert first.added, "第一次应补齐缺列"

    async with engine.begin() as conn:
        second = await sync_missing_columns(conn)
    assert not second.added and not second.skipped, (
        f"第二次不该再有任何变更，实际 added={second.added} skipped={second.skipped}"
    )
    await engine.dispose()


async def test_结构一致时不做任何变更(tmp_path):
    """全新库（结构与模型一致）不应产生任何 DDL。"""
    dbfile = tmp_path / "fresh.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        result = await sync_missing_columns(conn)
    await engine.dispose()

    assert not result.added and not result.skipped
    assert "无需变更" in result.summary()


async def test_空库不在这里建表而是交给_create_all(tmp_path):
    """表本身不存在时不处理 —— 建表是 create_all 的职责，避免重复实现。"""
    dbfile = tmp_path / "empty.db"
    sqlite3.connect(str(dbfile)).close()  # 空库

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.begin() as conn:
        result = await sync_missing_columns(conn)
    await engine.dispose()

    assert not result.added and not result.skipped, "表不存在时应全部跳过"


# ---------------------------------------------------------------------------
# 二、安全边界：只增列，且不该动的列坚决不动
# ---------------------------------------------------------------------------


async def test_只增列不删列不改类型(tmp_path):
    """同步必须是"只增"的 —— 绝不能删掉数据库里已有的列或改变类型。"""
    dbfile = await _make_legacy_database(tmp_path)
    before = _columns_of(dbfile, "job")

    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    async with engine.begin() as conn:
        await sync_missing_columns(conn)
    await engine.dispose()

    after = _columns_of(dbfile, "job")
    # 原有的列一个都不能少
    assert set(before) <= set(after), (
        f"同步过程删掉了原有列：{set(before) - set(after)}"
    )
    # 只允许新增，不允许改名/改序
    assert len(after) == len(before) + 1


def test_主键列不自动补齐():
    """主键不能靠 ADD COLUMN 补（已有行无法确定主键值）。"""
    table = Table("t_pk", MetaData(), Column("id", Integer, primary_key=True))
    allowed, reason = _can_auto_add(table.c.id)
    assert not allowed
    assert "主键" in reason


def test_唯一约束列不自动补齐():
    """唯一约束可能被已有数据违反 → 必须人工判断。"""
    column = Column("code", String(32), unique=True)
    allowed, reason = _can_auto_add(column)
    assert not allowed
    assert "唯一" in reason


def test_非空且无服务端默认值的列不自动补齐():
    """NOT NULL 且无 server_default 时，已有行填不出值 → 不能自动加。"""
    column = Column("required_field", String(32), nullable=False)
    allowed, reason = _can_auto_add(column)
    assert not allowed
    assert "NOT NULL" in reason


def test_可空列允许自动补齐():
    """nullable 的列可以安全补齐（已有行取 NULL）。"""
    column = Column("optional_field", String(32), nullable=True)
    allowed, _ = _can_auto_add(column)
    assert allowed


async def test_无法自动补齐的列会被报告而不是静默跳过(tmp_path):
    """不能补齐的列必须出现在 skipped 里 —— 静默跳过等于把 500 留给用户。

    构造一张真实存在但缺两列的表：一列可安全补（nullable），
    一列不可自动补（NOT NULL 且无 server_default）。两条路径一次验证。
    """
    dbfile = tmp_path / "partial.db"
    con = sqlite3.connect(str(dbfile))
    con.execute("CREATE TABLE temp_schema_probe (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()

    # 临时把这张表挂进 metadata（模拟"代码里定义了但库里没有"）
    temp_table = Table(
        "temp_schema_probe",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        Column("ok_col", String(16), nullable=True),
        Column("bad_col", String(16), nullable=False),
    )
    try:
        engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile.as_posix()}")
        async with engine.begin() as conn:
            result = await sync_missing_columns(conn)
        await engine.dispose()

        assert ("temp_schema_probe", "ok_col") in result.added, "可空列应被自动补齐"
        assert not any(c == "bad_col" for _, c in result.added), "不该硬加 NOT NULL 无默认值的列"
        skipped_cols = {c: why for _, c, why in result.skipped}
        assert "bad_col" in skipped_cols, "无法自动处理的列必须被报告"
        assert skipped_cols["bad_col"], "报告必须带原因，便于写进日志"
        # 报告要能读出人话
        assert "人工处理" in result.summary()
    finally:
        # metadata 是全局状态，必须还原，否则污染其它测试
        Base.metadata.remove(temp_table)


# ---------------------------------------------------------------------------
# 三、端到端：真实启动链路必须接上自检
# ---------------------------------------------------------------------------
#
# 上面的用例都直接调用 ``sync_missing_columns``，因此**测不到「接线」** ——
# 如果哪天 lifespan 里那句调用被删掉或挪到 create_all 之前，函数级测试依然全绿，
# 而缺陷会原样回归。接线恰恰是最容易被误删的部分，所以必须有一条端到端用例
# 走真实的 lifespan，断言「旧库启动后列真的被补上了」。

LEGACY_STARTUP_SCRIPT = """
import asyncio
import sqlite3
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, engine
import app.models  # noqa: F401

DBFILE = sys.argv[1]


async def main():
    # 1) 建出完整结构
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    # 2) 删掉一列，得到「代码升级了、库没跟上」的旧库
    con = sqlite3.connect(DBFILE)
    con.execute("ALTER TABLE job DROP COLUMN last_checked_at")
    con.commit()
    con.close()

    before = [r[1] for r in sqlite3.connect(DBFILE).execute("PRAGMA table_info(job)")]
    assert "last_checked_at" not in before, "前置条件：旧库应缺该列"

    # 3) 走真实启动链路（lifespan 里应有 schema 自检）
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        pass

    # 4) 报告结果
    after = [r[1] for r in sqlite3.connect(DBFILE).execute("PRAGMA table_info(job)")]
    print("LEGACY_STARTUP_OK", "last_checked_at" in after)


asyncio.run(main())
"""


def test_旧库启动时会自动补列(tmp_path):
    """真实 lifespan 必须执行 schema 自检（保护「接线」不被误删）。"""
    import os
    import subprocess
    import sys

    dbfile = tmp_path / "legacy_startup.db"
    env = {
        **os.environ,
        "DB_URL": f"sqlite+aiosqlite:///{dbfile.as_posix()}",
        "JWT_SECRET": "legacy-startup-secret-key-long-enough-to-pass",
        # 关掉会联网/扫库的后台任务，只验证结构自检这条链路
        "JOB_CHECKER_ENABLED": "false",
        "SCHEDULED_CRAWL_ENABLED": "false",
        "REPORT_ENABLED": "false",
        "AI_USAGE_LOG_ENABLED": "false",
    }

    result = subprocess.run(
        [sys.executable, "-c", LEGACY_STARTUP_SCRIPT, str(dbfile)],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=170,
    )

    assert result.returncode == 0, (
        "旧库启动失败：\n"
        f"--- stdout ---\n{result.stdout[-1500:]}\n"
        f"--- stderr ---\n{result.stderr[-2500:]}"
    )
    assert "LEGACY_STARTUP_OK True" in result.stdout, (
        "启动链路没有把缺列补上 —— 检查 lifespan 里是否仍调用 sync_missing_columns，"
        "以及它是否排在 create_all 之后。\n"
        f"--- stdout ---\n{result.stdout[-1500:]}"
    )
