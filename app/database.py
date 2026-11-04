import os
from pathlib import PurePath, PureWindowsPath

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import quote_plus

from app.config import settings


def is_absolute_db_path(name: str) -> bool:
    """判断 sqlite 库路径是不是绝对路径（**跨平台**，不看当前操作系统）。

    为什么不能用 `startswith(("/", "\\\\", "./", "C:\\\\", "c:\\\\"))`：
    那份白名单只认 C 盘和 POSIX 根。装在 **D 盘** 的绿色版（`db_name` 由
    `Path(sys.executable).parent / "recsys.db"` 拼出来，形如 `D:\\Apps\\RecSys\\recsys.db`）
    会掉进"相对路径"分支，变成 `sqlite+aiosqlite:///./D:\\Apps\\RecSys\\recsys.db` ——
    Windows 的文件名不允许冒号，结果是**双击 exe 直接起不来**。
    这不是假想：`db_name` 的打包默认值就是这么拼的，换个安装盘就触发。

    这里同时按 Windows 与 POSIX 两种语法判，因为 CI 跑在 Linux 而发布在 Windows，
    判据必须在两个平台上给出同一个答案（否则又是一类"本地永远复现不出"）。
    """
    if not name:
        return False
    win = PureWindowsPath(name)
    # `is_absolute()` 在 Windows 语义下要求「有盘符」；`/data/x.db` 这种**只有根**的路径
    # （Docker 卷常见）被判成相对，所以根非空也算绝对 —— 与旧白名单里的 "/" 等价。
    return win.is_absolute() or bool(win.root) or PurePath(name).is_absolute()


def sqlite_target(db_name: str) -> tuple:
    """由 `db_name` 算出 `(DATABASE_URL, 实际文件路径)`。

    相对路径统一挂到 `./` 下，避免在非根目录启动时落到奇怪位置；
    `:memory:` 之类的内存库原样透传（否则会被拼成 `./:memory:`，变成一个真文件）。
    """
    name = (db_name or "recruitment.db").strip()
    if name.startswith(":memory:") or name.startswith("file::memory:"):
        return f"sqlite+aiosqlite:///{name}", name
    if is_absolute_db_path(name):
        return f"sqlite+aiosqlite:///{name}", name
    return f"sqlite+aiosqlite:///./{name}", f"./{name}"


if settings.db_url:
    DATABASE_URL = settings.db_url
else:
    encoded_password = quote_plus(settings.db_password)
    db_type = (settings.db_type or "mysql").strip().lower()
    if db_type == "postgres":
        # PostgreSQL 需要 asyncpg 驱动（见 requirements.txt）
        DATABASE_URL = (
            f"postgresql+asyncpg://{settings.db_username}:{encoded_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )
    elif db_type == "sqlite":
        DATABASE_URL, _db_file = sqlite_target(settings.db_name)
        # SQLite 是文件库：父目录不存在时 sqlite3 直接报
        # "unable to open database file"，首次启动 / 干净机器必然失败。
        # 在此统一保证目录存在，脚本 / 服务 / 打包三种入口都受益
        #（此前只在 run_entry.py 修了打包场景，脚本入口仍会踩）。
        if _db_file and ":memory:" not in _db_file:
            _parent = os.path.dirname(os.path.abspath(_db_file))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
    else:
        DATABASE_URL = f"mysql+aiomysql://{settings.db_username}:{encoded_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"
def engine_kwargs_for(url: str) -> dict:
    """按方言裁剪连接池参数。

    SQLite 不支持 pool_size / max_overflow：内存库走 StaticPool、
    文件库走 SingletonThreadPool，传这两个参数会直接 TypeError：
        Invalid argument(s) 'pool_size','max_overflow' sent to create_engine()
    而测试环境与「通用版」绿色软件都以 SQLite 为默认库。
    SQLite 是本地文件库，本来也不需要 20 连接池。
    """
    return {} if url.startswith("sqlite") else {"pool_size": 20, "max_overflow": 5}


# ---------------------------------------------------------------------------
# SQLite 写锁：并发写偶发 500 `database is locked` 的修复（D-6）
#
# **实测到的机制**（scripts/stress_sqlite_writes.py，10 并发 × 20 轮）：
#   - 失败率 25%~47%（每次跑都复现，方差来自线程调度）
#   - 失败操作的耗时 min 6114ms / 中位 6315ms / max 6457ms
#     → **不是立刻被拒，而是等满了 busy_timeout 才放弃**
#   - 成功操作的耗时中位数 3634ms → 几乎每个写操作都在排队等写锁
#
# 也就是说：SQLite 的写锁是**排队**的（同一时刻只有一个写事务），
# 而 `log_action` 用独立会话写 `sys_log`、业务写事务同时占着锁，
# 排在后面的写事务把 5 秒的等待预算耗光 → 抛 database is locked → 接口 500。
#
# **为什么选「调大 busy_timeout + 开 WAL」而不是别的**：
#   - 调大 busy_timeout：让排队的写事务**等到**而不是失败。改动最小、
#     语义最清楚，且对 MySQL/PG 零影响（只包在 sqlite 分支里）。
#   - 开 WAL：读写不再互相阻塞（原来读锁会挡住写事务提交），
#     写队列变短，等锁时间随之下降；顺带解决「读多写少时读卡住写」。
#   - 不选「日志改批量异步写」：要改 app/utils/log_decorator.py 的写路径，
#     收益有限（业务写之间的争用依然存在），改动面反而更大。
#   - 不选「只给 log_action 加重试」：只治了日志那一路，业务写之间的
#     争用照样 500 —— 而 500 是从业务写那一侧冒出来的。
#
# **仍然会失败的情况（边界要说清楚）**：
#   1. 单个写事务持锁超过 busy_timeout（本项目设 30 秒）—— 等待耗尽仍会 500；
#   2. WAL 不支持网络文件系统（SMB/NFS），此时 journal_mode 仍是 delete，
#      只剩 busy_timeout 起作用；
#   3. 同一事务里「先读后写」的锁升级：SQLite 为避免死锁会立即返回
#      SQLITE_BUSY（不触发 busy handler），本修复覆盖不到 ——
#      根治要改成 BEGIN IMMEDIATE，属于更大的改动，本轮未做。
# ---------------------------------------------------------------------------

# SQLite 等锁预算：30 秒。实测 5 秒时 25%~47% 的并发写会等满预算而失败。
SQLITE_BUSY_TIMEOUT_MS = 30000


def sqlite_connect_args(url: str) -> dict:
    """SQLite 专用连接参数；非 SQLite 方言返回空 dict（行为完全不变）。

    单独成函数而不是塞进 ``engine_kwargs_for``：那个函数的契约是
    「SQLite 不带连接池参数」，有测试逐字断言它返回 ``{}``
    （tests/test_db_engine_dialect.py），把连接参数混进去会破坏那条契约。
    """
    if not url.startswith("sqlite"):
        return {}
    # sqlite3.connect(timeout=…) 即 busy_timeout（毫秒），默认只有 5 秒
    return {"connect_args": {"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000}}


def tune_sqlite_connection(dbapi_connection, _record) -> None:
    """SQLite 连接建立后设置 PRAGMA：只加长等锁预算，**不碰 journal_mode**。

    做成独立函数（而不是写死在监听器里）是为了让测试能直接复用同一份逻辑 ——
    否则测试只能验证「自己写的副本」，测不到生产真正在用的那段配置。

    ⚠️ 2026-11-04 撤销「每连接执行 PRAGMA journal_mode=WAL」：
    WAL 在**本机**并发写下会随机抛 ``sqlite3.OperationalError: attempt to
    write a readonly database``（不是锁等待，busy_timeout 救不了）。隔离实测：

    - 纯 sqlite3（不经 SQLAlchemy），WAL + 8 线程并发写 → 5/5 轮出错
    - 同一份代码改成 DELETE 日志模式          → 0/5 轮出错
    - DELETE + busy_timeout=30s，10 线程×20 轮 → 0/3 轮出错，P95 212ms

    也就是说，锁冲突**只靠加长 busy_timeout 就够**，WAL 是多余且有害的一项。
    另外 journal_mode 是**持久属性**（写进库头），本来就不该每建一个连接设一次：
    并发建连时多个连接同时请求切换日志模式，是竞态的直接来源。
    已有库若是 WAL，由启动时的一次性 ``ensure_delete_journal_mode()`` 切回。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


async def ensure_delete_journal_mode(conn) -> str | None:
    """把已有库从 WAL 切回 DELETE 日志模式（幂等，启动时调用一次）。

    为什么需要：``journal_mode`` 是持久属性，撤销「每连接设 WAL」只影响新库；
    上一版已经在库里写下了 ``wal``，那几个库仍会保持 WAL 并继续偶发 readonly。
    在**启动阶段、并发尚未开始**时切一次，不存在竞态。
    非 SQLite 方言直接跳过；返回切换后的模式（便于启动日志回读）。
    """
    if not DATABASE_URL.startswith("sqlite"):
        return None
    from sqlalchemy import text

    current = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    if str(current).lower() == "wal":
        await conn.execute(text("PRAGMA journal_mode=DELETE"))
        current = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    return str(current).lower()


engine = create_async_engine(
    DATABASE_URL,
    **engine_kwargs_for(DATABASE_URL),
    **sqlite_connect_args(DATABASE_URL),
)
if DATABASE_URL.startswith("sqlite"):
    event.listen(engine.sync_engine, "connect", tune_sqlite_connection)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
