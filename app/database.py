import os
from pathlib import PurePath, PureWindowsPath

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


engine = create_async_engine(DATABASE_URL, **engine_kwargs_for(DATABASE_URL))
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
