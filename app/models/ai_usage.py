"""AI 调用用量记录：每次 LLM / embedding 调用的 token 消耗、耗时与降级路径。

**为什么需要它**：只做限流并不能回答「成本到底花在哪」。只有把每次调用落库，
才能回答三个必须能答的问题：

1. 花了多少？—— 按模型/场景/天聚合 token 与折算费用
2. 贵在哪？—— 对话、工具识别、分析报告、向量化哪个环节占比最高
3. 降级真的生效了吗？—— 云端失败转本地时，记录 `tier` 变化与失败原因

设计上刻意与业务解耦：写入失败只记日志、绝不影响主流程（见
``app/services/usage_service.py``），因为「统计」永远不该拖垮「业务」。
"""
from sqlalchemy import Column, Integer, String, DateTime, Float, Index
from sqlalchemy.sql import func

from app.database import Base


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 触发调用的用户；内部任务（如分析报告、评估）允许为空
    user_id = Column(Integer)

    # 调用场景：chat 对话 / tool_detect 工具识别 / analysis 分析报告 / embedding 向量化
    scene = Column(String(32), nullable=False, default="chat")

    # 服务商与模型：provider 用于区分计费口径，model 用于区分单价
    provider = Column(String(32))          # deepseek / zhipu / ollama / local_fallback
    model = Column(String(64))

    # 降级路径：cloud 云端 / local 本地模型 / fallback 规则引擎
    # 「云端失败转本地」这类降级会体现为同一轮对话里不同的 tier 记录
    tier = Column(String(16))

    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)

    latency_ms = Column(Integer, default=0)
    success = Column(Integer, default=1)
    error_msg = Column(String(255))

    # 按本次用量折算的费用（元），落库时算好，避免查询时重复计算
    cost = Column(Float, default=0.0)

    created_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_ai_usage_scene_created", "scene", "created_at"),
        Index("ix_ai_usage_provider_model", "provider", "model"),
    )
