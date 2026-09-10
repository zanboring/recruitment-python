from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class KnowledgeBase(Base):
    """知识库条目。

    索引说明（此前完全没有索引）：
    - ``status``：**每一次 AI 对话**的 RAG 检索都是 ``WHERE status = 1``，
      缺索引时每次提问都触发一次全表扫描；知识库越大，对话延迟越高。
    - ``source``：统计「人工 / 自动入库」占比时按它分组。
    """

    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(String(500), nullable=False)
    answer = Column(Text, nullable=False)
    tags = Column(String(300))
    source = Column(String(20), default="manual")
    usage_count = Column(Integer, default=0)
    status = Column(Integer, default=1)
    quality_score = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_knowledge_status", "status"),
        Index("ix_knowledge_source", "source"),
    )
