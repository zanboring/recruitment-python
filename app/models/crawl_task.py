from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class CrawlTask(Base):
    """爬取任务。索引支撑任务列表（按创建时间倒序）与状态过滤。"""

    __tablename__ = "crawl_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_site = Column(String(50), nullable=False)
    keyword = Column(String(100), nullable=False)
    city = Column(String(50))
    status = Column(String(20), default="PENDING")
    job_count = Column(Integer, default=0)
    message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_crawl_task_created_at", "created_at"),
        Index("ix_crawl_task_status", "status"),
    )
