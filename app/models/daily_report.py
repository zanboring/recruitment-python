from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class DailyReport(Base):
    """每日自动化日报：定时聚合岗位统计 → 生成 Excel → AI 摘要 → 落库。

    设计要点：
    - ``report_date`` 唯一：同一天重复生成时直接复用（幂等），
      避免定时任务与手动触发并发时产生两份同日报纸；
    - ``stats_json`` 保存生成时的统计快照，即使后续数据变化，
      历史日报仍保持「当天看到的样子」；
    - ``ai_summary`` 与 ``generated_by`` 分离：LLM 不可用时降级为
      纯统计文本，但状态如实标注，不假装有 AI 能力。
    """

    __tablename__ = "daily_report"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 北京时区日期（YYYY-MM-DD），唯一约束保证一天一张
    report_date = Column(String(10), nullable=False, unique=True)
    title = Column(String(200))
    # AI 生成的摘要正文（LLM 不可用时为规则统计文本）
    ai_summary = Column(Text)
    # 实际生成层：primary(云端) / local(Ollama) / rule(规则引擎兜底)
    generated_by = Column(String(20), default="rule")
    # 生成时的统计快照（JSON 字符串）
    stats_json = Column(Text)
    # 生成的 Excel 报表相对路径（app 目录内）
    excel_path = Column(String(300))
    # 当日新增 / 在架 / 累计
    new_jobs = Column(Integer, default=0)
    active_jobs = Column(Integer, default=0)
    total_jobs = Column(Integer, default=0)
    status = Column(String(20), default="GENERATED")  # GENERATED / FAILED
    message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_daily_report_date", "report_date"),
    )