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
