"""数据库引擎构造的方言适配回归测试。

背景：``app/database.py`` 在**模块导入时**就 ``create_async_engine(DATABASE_URL)``，
原先无条件传 ``pool_size=20, max_overflow=5``。SQLite 的内存库走 StaticPool、
文件库走 SingletonThreadPool，两者都不接受这两个参数，于是：

    TypeError: Invalid argument(s) 'pool_size','max_overflow' sent to
               create_engine(), using configuration
               SQLiteDialect_aiosqlite/StaticPool/Engine

后果是任何以 ``sqlite:///:memory:`` 为库的场景（最典型是测试环境）
在 collection 阶段就 ImportError，且报错点离真正原因很远。

这里锁死行为，防止后人「顺手」把 pool 参数加回无条件位置。
"""
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import engine_kwargs_for


def test_sqlite_memory_url_gets_no_pool_kwargs():
    """内存库不得带连接池参数（StaticPool 不支持）。"""
    assert engine_kwargs_for("sqlite+aiosqlite:///:memory:") == {}


def test_sqlite_file_url_gets_no_pool_kwargs():
    """文件库同样按 sqlite 方言裁剪，保持单一判断口径。"""
    assert engine_kwargs_for("sqlite+aiosqlite:///./recruitment.db") == {}
    assert engine_kwargs_for("sqlite+aiosqlite:///C:/tmp/recruitment.db") == {}


def test_non_sqlite_url_keeps_pool_kwargs():
    """MySQL / PostgreSQL 仍要连接池，不能被误删。"""
    assert engine_kwargs_for("mysql+aiomysql://u:p@localhost:3306/db") == {
        "pool_size": 20,
        "max_overflow": 5,
    }
    assert engine_kwargs_for("postgresql+asyncpg://u:p@localhost:5432/db") == {
        "pool_size": 20,
        "max_overflow": 5,
    }


def test_sqlite_memory_engine_can_actually_be_created():
    """端到端回归：真正拿 SQLite 内存 URL 建一次引擎，不许抛异常。"""
    url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(url, **engine_kwargs_for(url))
    assert engine.dialect.name == "sqlite"


# ---------------------------------------------------------------------------
# 跨进程写回归（2026-11-20）
#
# compose 默认 `--workers 2`，两个进程共享一个 SQLite 文件；此前仓库里的并发写
# 用例**全是单进程 asyncio**，写锁的跨进程路径零覆盖。这里的轻量版（2 进程 × 10 次）
# 是回归防线：谁改了 busy_timeout / journal_mode / 连接配置，这条会红。
#
# 用 subprocess 跑脚本的 CLI 而不是在 pytest 里 import 脚本再调 run_cross_process：
# Windows 的 spawn 子进程要能按模块名 import worker 函数，而 importlib 动态加载的
# 模块名在子进程里不可见 —— 那样测的就不是集成端会跑的那条命令了。
# ---------------------------------------------------------------------------


def test_跨进程两worker各写十次没有锁冲突(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "cross_process.db"
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "stress_sqlite_writes.py"),
            "--processes", "2",
            "--rounds", "10",
            "--hold-ms", "0",
            "--db", str(db_path),
            "--json",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    result = json.loads(proc.stdout)

    assert result["mode"] == "cross_process"
    assert (result["processes"], result["rounds"]) == (2, 10)
    assert result["total_ops"] == 20
    assert result["success_ops"] == 20
    assert result["locked_errors"] == 0, f"出现锁冲突：{result['sample_errors']}"
    assert result["readonly_errors"] == 0, f"出现 WAL 式 readonly：{result['sample_errors']}"
    assert result["other_errors"] == 0, result["sample_errors"]
    # 生产配置必须在这条路径上真的生效（不是只在单进程模式里）
    assert result["sqlite"]["journal_mode"] == "delete"
    assert result["sqlite"]["busy_timeout"] == 30000
    assert "p95_ms" in result["ok_duration"]
