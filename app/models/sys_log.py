from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class SysLog(Base):
    """操作日志。

    索引说明（此前完全没有索引，实测两条最常用的查询都要全表扫描）：
    - ``created_at``：日志清理 ``DELETE WHERE created_at < ?`` 与列表页
      ``ORDER BY created_at DESC`` 都依赖它。日志表是所有表里增长最快的
      （每个写操作都落一条），缺索引时每次翻页都会「全表扫描 + 临时 B 树排序」，
      随数据量增长退化最快。
    - ``username + created_at`` 复合索引：列表页按操作人过滤并倒序翻页。
    """

    __tablename__ = "sys_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50))
    action = Column(String(255))
    method = Column(String(255))
    uri = Column(String(255))
    ip = Column(String(50))
    params = Column(Text)
    success = Column(Integer, default=1)
    error_msg = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_sys_log_created_at", "created_at"),
        Index("ix_sys_log_username_created", "username", "created_at"),
    )
