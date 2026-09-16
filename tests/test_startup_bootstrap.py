"""启动自举的端到端回归测试。

**背景（这是一个真实缺陷的回归锁）**

「建表」原先只写在 ``scripts/init_db.py`` 里。源码模式有 ``start-generic.bat``
和文档步骤保证先执行它；但**打包后的 exe 没有任何入口建表**，而发布包里
（``dist/RecSys/``）也不带预置数据库。结果是：干净机器上双击 ``RecSys.exe``
→ lifespan → ``init_default_admin`` 先 ``SELECT ... FROM user``
→ ``sqlite3.OperationalError: no such table: user``
→ ``Application startup failed. Exiting.``

也就是说「任意电脑绿色软件，零依赖即用」这个交付承诺在干净机器上不成立，
而全部 545 项测试都是绿的 —— 因为没有任何用例真正走过一次「空库启动」。

本文件用**子进程**跑一次真实的 lifespan：空库 → 建表 → 建默认管理员，
这是唯一能覆盖该缺陷的方式（进程内的 conftest 已经把数据库指向内存库并
建好表了，永远测不到「空库」这一前提）。
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BOOTSTRAP_SCRIPT = textwrap.dedent(
    """
    import asyncio
    from sqlalchemy import inspect

    from app.main import create_app
    from app.database import engine


    async def main():
        app = create_app()
        # 走真实的 lifespan：建表 → 起调度器 → 起存活核查 → 建默认管理员
        async with app.router.lifespan_context(app):
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        expected = {"user", "job", "daily_report", "knowledge_base", "ai_usage", "sys_log", "crawl_task"}
        missing = expected - set(tables)
        assert not missing, f"启动后缺少表：{missing}；实际：{sorted(tables)}"
        print("BOOTSTRAP_OK", len(tables))


    asyncio.run(main())
    """
)



def test_空库启动能自动建表并建出默认管理员(tmp_path):
    """干净机器（无任何数据库文件）上启动必须成功。"""
    db_file = tmp_path / "fresh_bootstrap.db"
    assert not db_file.exists(), "前置条件：数据库文件必须不存在"

    env = {
        **os.environ,
        # 指向一个全新的 SQLite 文件 = 模拟「刚拷到新电脑」
        "DB_URL": f"sqlite+aiosqlite:///{db_file.as_posix()}",
        "JWT_SECRET": "bootstrap-test-secret-key-should-be-long-enough",
        # 关掉会去访问网络的后台任务，只验证启动自举本身
        "JOB_CHECKER_ENABLED": "false",
        "SCHEDULED_CRAWL_ENABLED": "false",
        "REPORT_ENABLED": "false",
        "AI_USAGE_LOG_ENABLED": "false",
    }

    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP_SCRIPT],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=170,
    )

    assert result.returncode == 0, (
        "空库启动失败（通用版在干净机器上会起不来）：\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-3000:]}"
    )
    assert "BOOTSTRAP_OK" in result.stdout

    # 数据库文件确实被创建出来，且默认管理员写入成功
    assert db_file.exists() and db_file.stat().st_size > 0



def test_启动自举是幂等的(tmp_path):
    """第二次启动不得因为「表已存在」而失败（create_all 必须幂等）。"""
    db_file = tmp_path / "twice.db"
    env = {
        **os.environ,
        "DB_URL": f"sqlite+aiosqlite:///{db_file.as_posix()}",
        "JWT_SECRET": "bootstrap-test-secret-key-should-be-long-enough",
        "JOB_CHECKER_ENABLED": "false",
        "SCHEDULED_CRAWL_ENABLED": "false",
        "REPORT_ENABLED": "false",
        "AI_USAGE_LOG_ENABLED": "false",
    }

    for attempt in range(2):
        result = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP_SCRIPT],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=170,
        )
        assert result.returncode == 0, (
            f"第 {attempt + 1} 次启动失败：\n{result.stderr[-3000:]}"
        )
