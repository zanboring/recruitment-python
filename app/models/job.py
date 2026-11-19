from sqlalchemy import Column, Integer, String, DECIMAL, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class Job(Base):
    __tablename__ = "job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer)
    title = Column(String(200), nullable=False)
    company_name = Column(String(200))
    source_site = Column(String(50), nullable=False)
    job_key = Column(String(100), nullable=False, unique=True)
    job_status = Column(String(20), default="ACTIVE")
    city = Column(String(50))
    experience = Column(String(50))
    education = Column(String(50))
    min_salary = Column(DECIMAL(15, 2))
    max_salary = Column(DECIMAL(15, 2))
    salary_unit = Column(String(10), default="元")
    skills = Column(String(500))
    # 每个技能标签的证据来源（2026-11-24 新增）。
    # 编码为 ``|title:Java|desc:MySQL|`` —— 与 skills 的逗号串一一对应，
    # 前后都带 | 是为了能精确查「哪些标签来自标题」：
    #   SELECT ... WHERE skills_sources LIKE '%|title:Java|%'
    # 不改 skills 的语义；NULL / 空串表示尚未标注（老数据、或爬虫新写入的行）。
    skills_sources = Column(String(500))
    job_desc = Column(Text)
    url = Column(String(500))
    detail_html = Column(Text)
    publish_time = Column(DateTime)
    last_seen_at = Column(DateTime)
    # 岗位存活核查时间（job_checker 周期性刷新；NULL = 从未核查）
    last_checked_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_source_site", "source_site"),
        Index("idx_city", "city"),
        Index("idx_job_status", "job_status"),
        Index("idx_created_at", "created_at"),
    )
