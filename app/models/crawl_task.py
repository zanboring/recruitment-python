from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class CrawlTask(Base):
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
