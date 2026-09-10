"""RAG 检索评估执行器：多策略对比 + 指标输出。

**设计要点**

1. **内存库隔离**：每次评估都新建一个干净的 SQLite 内存库并灌入黄金集知识库。
   直接在项目库上评估有两个问题 —— 生产知识库内容会持续变化导致结果不可复现，
   且检索本身会写库（usage_count 自增）污染真实数据。
2. **策略可插拔**：把「检索策略」统一抽象为「查询 → 条目 ID 列表」，
   keyword / semantic / hybrid 三种策略共用同一套指标计算逻辑，
   因此新增策略只需加一个分支，评估代码零改动。
3. **优雅降级**：缺少 API 密钥时跳过需要向量化的策略并说明原因，
   而不是让整个评估失败 —— 这样在离线环境下依然能跑出关键词基线。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - 导入以把模型注册进 Base.metadata
from app.database import Base
from app.evaluation.golden_set import KNOWLEDGE_SEED, QUERIES
from app.evaluation.metrics import evaluate_queries
from app.models.knowledge_base import KnowledgeBase
from app.services import knowledge_service
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

# 评估统一按 Top-5 截断，与线上注入 AI 上下文的条目数保持一致
TOP_K = 5

# 需要调用向量化接口（即需要 ZHIPUAI_API_KEY）的策略
_NEEDS_EMBEDDING = {"semantic", "hybrid"}

ALL_STRATEGIES = ["keyword", "semantic", "hybrid"]


async def _build_eval_session():
    """构造一个灌好黄金集知识库的内存库，返回 (engine, sessionmaker)。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all([
            KnowledgeBase(
                question=item["question"],
                answer=item["answer"],
                tags=item["tags"],
                source="seed",
                quality_score=5,
                status=1,
            )
            for item in KNOWLEDGE_SEED
        ])
        await db.commit()
    return engine, maker


async def _retrieve_ids(strategy: str, db, query: str) -> list:
    """按策略执行一次检索，返回条目 ID 列表（按相关性降序）。"""
    if strategy == "keyword":
        items = await KnowledgeService._keyword_search(db, query)
    elif strategy == "semantic":
        items = await KnowledgeService._semantic_search(db, query, top_k=TOP_K)
    elif strategy == "hybrid":
        items = await KnowledgeService._hybrid_search(db, query, top_k=TOP_K)
    else:
        raise ValueError(f"未知检索策略: {strategy}")
    return [item.id for item in items]


async def _probe_embedding() -> tuple:
    """真实调用一次向量化，探测后端是否可用。

    只检查「有没有配置密钥」是不够的 —— 本地 Ollama 可能未拉取向量模型，
    或服务端未启用 embeddings 接口。若不做探测，这类失败会被逐查询吞掉，
    最终表现为「语义检索命中率 0%」，把**环境问题误报成算法效果差**。
    """
    from app.services.embedding_service import embed_texts

    try:
        await embed_texts(["向量化可用性探测"])
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"向量化后端不可用：{type(e).__name__}: {str(e)[:120]}"


async def run_evaluation(strategies: list = None, top_k: int = TOP_K) -> dict:
    """跑完整评估，返回各策略的指标。

    返回结构：
        {
          "top_k": 5, "query_count": 28, "knowledge_count": 20,
          "results": {"keyword": {...指标...}, "semantic": {...}},
          "skipped": {"semantic": "跳过原因"},
        }
    """
    strategies = strategies or list(ALL_STRATEGIES)
    engine, maker = await _build_eval_session()

    try:
        # 黄金集的 targets 用 question 原文标注，这里映射成数据库真实 ID
        async with maker() as db:
            rows = (await db.execute(select(KnowledgeBase))).scalars().all()
            question_to_id = {row.question: row.id for row in rows}

        results: dict = {}
        skipped: dict = {}

        embedding_ok, embedding_reason = await _probe_embedding()

        for strategy in strategies:
            if strategy in _NEEDS_EMBEDDING and not embedding_ok:
                skipped[strategy] = embedding_reason
                continue

            # 换库后必须清掉条目缓存，否则会拿到上一个库的 ORM 对象
            knowledge_service._invalidate_caches()

            records = []
            async with maker() as db:
                for item in QUERIES:
                    relevant = {question_to_id[t] for t in item["targets"] if t in question_to_id}
                    try:
                        retrieved = await _retrieve_ids(strategy, db, item["query"])
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[%s] 查询 %r 检索失败：%s", strategy, item["query"], e)
                        retrieved = []
                    records.append((retrieved, relevant, item["category"]))

            results[strategy] = evaluate_queries(records, ks=(1, 3, top_k))
    finally:
        await engine.dispose()

    return {
        "top_k": top_k,
        "query_count": len(QUERIES),
        "knowledge_count": len(KNOWLEDGE_SEED),
        "results": results,
        "skipped": skipped,
    }
