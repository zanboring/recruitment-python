"""统一的 UTC 时间工具。

背景：数据库的 DATETIME 列不保存时区信息，MySQL / SQLite 驱动读回来的都是
naive datetime；而 datetime.now(timezone.utc) 是 aware datetime。两者直接比较
会抛 TypeError("can't compare offset-naive and offset-aware datetimes")。

曾踩过的坑：AuthService 用 aware 时间去比较库里的 locked_until，导致用户一旦
触发登录锁定，之后每次登录都 500。故全局约定：写入数据库的时间一律 naive UTC。
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回 naive UTC 当前时间（用于写入 DATETIME 列与和它比较）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
