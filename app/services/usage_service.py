"""AI 用量与成本服务：记录每次调用的 token 消耗，并按维度聚合。

回答三个必须能答的问题（这也是「AI 应用工程化」与「能跑通的 Demo」的分界线）：

1. **花了多少** —— 按模型 / 场景 / 日期聚合 token 与折算费用
2. **贵在哪** —— 对话、工具识别、分析报告、向量化各环节的占比
3. **降级生效了吗** —— 按降级层级统计调用次数与失败原因

三条设计原则：

- **写入永不影响业务**：``record`` 内部吞掉所有异常只记日志。统计失败不该让
  用户的对话失败 —— 这是「可观测性」与「正确性」的优先级取舍。
- **独立会话**：用量记录用自己的数据库会话，不复用请求会话，既避免污染
  业务事务，也避免一次统计写入的失败回滚掉业务数据。
- **费用落库而非查询时算**：单价可能调整，历史记录应按当时的单价计价，
  因此 ``cost`` 在写入时计算并持久化。
"""
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_usage import AiUsage
from app.utils.timeutil import utc_now

logger = logging.getLogger(__name__)

# ---- 场景常量 ----
SCENE_CHAT = "chat"                  # 用户对话
SCENE_TOOL_DETECT = "tool_detect"    # 工具识别（Function Calling 的前置判断）
SCENE_ANALYSIS = "analysis"          # 分析报告生成
SCENE_EMBEDDING = "embedding"        # 知识库向量化

# ---- 降级层级 ----
TIER_CLOUD = "cloud"
TIER_LOCAL = "local"
TIER_FALLBACK = "fallback"

# 会话工厂可注入，便于测试指向内存库而不触碰真实数据库
_session_factory = None


def set_session_factory(factory) -> None:
    """注入用量记录的会话工厂（测试用）。

    默认走 ``app.database.async_session``。这里不直接依赖全局 engine，
    是因为用量记录需要独立会话，而测试环境下业务会话是被覆盖过的。
    """
    global _session_factory
    _session_factory = factory


def _get_session_factory():
    if _session_factory is not None:
        return _session_factory
    from app.database import async_session

    return async_session


def _unit_price(provider: str) -> tuple:
    """返回 (输入单价, 输出单价)，单位：元 / 百万 token。

    本地模型计 0 元（无 API 费用），仅用于统计「省下多少云端调用」。
    """
    provider = (provider or "").lower()
    if provider == "deepseek":
        return (settings.usage_price_deepseek_input,
                settings.usage_price_deepseek_output)
    if provider == "zhipu":
        return (settings.usage_price_zhipu_input,
                settings.usage_price_zhipu_output)
    return (settings.usage_price_local, settings.usage_price_local)


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按服务商单价折算费用（元）。"""
    price_in, price_out = _unit_price(provider)
    return round(
        (prompt_tokens or 0) / 1_000_000 * price_in
        + (completion_tokens or 0) / 1_000_000 * price_out,
        6,
    )


async def record(
    *,
    scene: str,
    provider: str = "",
    model: str = "",
    tier: str = TIER_CLOUD,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    success: bool = True,
    error_msg: str = "",
    user_id: Optional[int] = None,
) -> None:
    """记录一次 AI 调用的用量。**任何异常都不会抛出。**

    ``AI_USAGE_LOG_ENABLED=false`` 可整体关闭，便于极端成本敏感场景。
    """
    if not getattr(settings, "ai_usage_log_enabled", True):
        return

    try:
        factory = _get_session_factory()
        async with factory() as session:
            session.add(AiUsage(
                user_id=user_id,
                scene=scene,
                provider=provider,
                model=(model or "")[:64],
                tier=tier,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                total_tokens=int((prompt_tokens or 0) + (completion_tokens or 0)),
                latency_ms=int(latency_ms or 0),
                success=1 if success else 0,
                error_msg=(error_msg or "")[:255] or None,
                cost=estimate_cost(provider, prompt_tokens, completion_tokens),
            ))
            await session.commit()
    except Exception as e:  # noqa: BLE001 - 统计失败绝不能影响业务
        logger.warning("用量记录写入失败（已忽略，不影响本次请求）：%s", e)


async def summary(db: AsyncSession, days: int = 7) -> dict:
    """按总览 / 场景 / 模型 / 日期 / 降级层级聚合用量。

    时间基准统一为 naive UTC（与 DATETIME 列的写入语义一致），避免 MySQL
    在东八区下产生 8 小时偏移导致「今天的量算到昨天」。
    """
    if days < 1:
        days = 1
    since = utc_now() - timedelta(days=days)

    base = select(
        func.count(AiUsage.id),
        func.coalesce(func.sum(AiUsage.prompt_tokens), 0),
        func.coalesce(func.sum(AiUsage.completion_tokens), 0),
        func.coalesce(func.sum(AiUsage.total_tokens), 0),
        func.coalesce(func.sum(AiUsage.cost), 0.0),
        func.coalesce(func.avg(AiUsage.latency_ms), 0),
        func.coalesce(func.sum(AiUsage.success), 0),
    ).where(AiUsage.created_at >= since)

    calls, prompt_t, completion_t, total_t, cost, avg_latency, ok = (
        await db.execute(base)
    ).one()

    by_scene = await _group(db, AiUsage.scene, since)
    by_model = await _group(db, AiUsage.model, since)
    by_provider = await _group(db, AiUsage.provider, since)
    by_tier = await _group(db, AiUsage.tier, since)
    by_day = await _daily(db, since)

    calls = int(calls or 0)
    # 本地模型不计费，但把它的 token 量按主服务商单价折算，回答「省了多少」
    local_tokens = sum(
        int(row["total_tokens"]) for row in by_tier if row["key"] == TIER_LOCAL
    )
    cloud_price_in, cloud_price_out = _unit_price(settings.ai_provider)
    saved = round(local_tokens / 1_000_000 * (cloud_price_in + cloud_price_out) / 2, 6)

    return {
        "days": days,
        "since": since.isoformat(),
        "totals": {
            "calls": calls,
            "prompt_tokens": int(prompt_t or 0),
            "completion_tokens": int(completion_t or 0),
            "total_tokens": int(total_t or 0),
            "cost": round(float(cost or 0.0), 6),
            "avg_latency_ms": int(avg_latency or 0),
            "success_rate": round(int(ok or 0) / calls, 4) if calls else 0.0,
            "local_tokens": local_tokens,
            "saved_cost_estimate": saved,
        },
        "by_scene": by_scene,
        "by_model": by_model,
        "by_provider": by_provider,
        "by_tier": by_tier,
        "by_day": by_day,
    }


async def _group(db: AsyncSession, column, since) -> list:
    """按某一列聚合：调用次数 / token / 费用 / 平均耗时。"""
    stmt = (
        select(
            column,
            func.count(AiUsage.id),
            func.coalesce(func.sum(AiUsage.total_tokens), 0),
            func.coalesce(func.sum(AiUsage.cost), 0.0),
            func.coalesce(func.avg(AiUsage.latency_ms), 0),
        )
        .where(AiUsage.created_at >= since)
        .group_by(column)
        .order_by(func.sum(AiUsage.total_tokens).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": row[0] or "unknown",
            "calls": int(row[1] or 0),
            "total_tokens": int(row[2] or 0),
            "cost": round(float(row[3] or 0.0), 6),
            "avg_latency_ms": int(row[4] or 0),
        }
        for row in rows
    ]


async def _daily(db: AsyncSession, since) -> list:
    """按天聚合。SQLite 与 MySQL 的日期函数不同，这里用 func.date 兼容两者。"""
    day = func.date(AiUsage.created_at)
    stmt = (
        select(
            day,
            func.count(AiUsage.id),
            func.coalesce(func.sum(AiUsage.total_tokens), 0),
            func.coalesce(func.sum(AiUsage.cost), 0.0),
        )
        .where(AiUsage.created_at >= since)
        .group_by(day)
        .order_by(day)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "date": str(row[0]),
            "calls": int(row[1] or 0),
            "total_tokens": int(row[2] or 0),
            "cost": round(float(row[3] or 0.0), 6),
        }
        for row in rows
    ]
