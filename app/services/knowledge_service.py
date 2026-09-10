import logging
import time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, update

from app.config import settings
from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.embedding_service import invalidate_embedding_cache

logger = logging.getLogger(__name__)

# 语义检索相似度阈值：低于该值的命中视为噪音，不注入上下文，改走关键词降级
SEMANTIC_THRESHOLD = 0.3

KEYWORDS = {
    "薪资", "推荐", "分析", "统计", "爬虫", "AI", "招聘", "岗位",
    "技能", "学历", "经验", "城市", "公司", "简历", "面试",
    "Spring", "Java", "Python", "Vue", "React", "MySQL", "Redis",
    "全栈", "后端", "前端", "算法", "数据", "可视化", "安全",
    "登录", "注册", "权限", "管理员", "用户", "知识库", "模型",
    "Ollama", "GLM", "降级", "流式", "SSE", "向量", "RAG"
}

_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 600


def _invalidate_caches() -> None:
    """知识库内容变更后统一失效两类缓存。

    - `_cache`：启用条目的列表缓存。不清的话新条目 600 秒内进不了检索池；
    - embedding 向量缓存：不清的话旧条目的向量最长 1 小时内仍参与相似度
      计算，表现为「知识改了却检索不到新内容」。
    """
    _cache["data"] = None
    invalidate_embedding_cache()


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class KnowledgeService:
    @staticmethod
    async def list_knowledge(db: AsyncSession, page_num: int, page_size: int):
        stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_all_enabled(db: AsyncSession):
        now = time.time()
        if _cache["data"] is not None and now - _cache["timestamp"] < CACHE_TTL:
            return _cache["data"]
        stmt = select(KnowledgeBase).where(KnowledgeBase.status == 1).order_by(KnowledgeBase.quality_score.desc(), KnowledgeBase.usage_count.desc())
        result = await db.execute(stmt)
        items = result.scalars().all()
        _cache["data"] = items
        _cache["timestamp"] = now
        return items

    @staticmethod
    async def search(db: AsyncSession, keyword: str):
        escaped = _escape_like(keyword)
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.status == 1,
            or_(
                KnowledgeBase.question.like(f"%{escaped}%", escape="\\"),
                KnowledgeBase.tags.like(f"%{escaped}%", escape="\\"),
                KnowledgeBase.answer.like(f"%{escaped}%", escape="\\")
            )
        ).order_by(KnowledgeBase.quality_score.desc()).limit(10)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_by_id(db: AsyncSession, knowledge_id: int):
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, request: KnowledgeBaseCreate):
        kb = KnowledgeBase(
            question=request.question,
            answer=request.answer,
            tags=request.tags,
            source=request.source,
            quality_score=request.quality_score
        )
        db.add(kb)
        await db.commit()
        _invalidate_caches()
        return kb

    @staticmethod
    async def update(db: AsyncSession, knowledge_id: int, request: KnowledgeBaseUpdate):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        if request.question:
            kb.question = request.question
        if request.answer:
            kb.answer = request.answer
        if request.tags:
            kb.tags = request.tags
        if request.quality_score is not None:
            kb.quality_score = request.quality_score
        await db.commit()
        _invalidate_caches()
        return kb

    @staticmethod
    async def delete(db: AsyncSession, knowledge_id: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        await db.delete(kb)
        await db.commit()
        _invalidate_caches()

    @staticmethod
    async def toggle_status(db: AsyncSession, knowledge_id: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        kb.status = 1 if kb.status == 0 else 0
        await db.commit()
        _invalidate_caches()
        return kb

    @staticmethod
    async def set_score(db: AsyncSession, knowledge_id: int, score: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        kb.quality_score = score
        await db.commit()
        _invalidate_caches()
        return kb

    @staticmethod
    async def get_context_with_sources(db: AsyncSession, keyword: str) -> tuple:
        """检索知识库并返回 ``(注入用的上下文, 引用来源列表)``。

        **为什么要单独返回 sources**：把资料塞进 prompt 里再说一句「请参考回答」，
        对使用者是完全不透明的 —— 他不知道模型是查到了资料还是凭空作答，
        也无从判断答案可不可信。返回来源后，前端可以把「依据」展示出来，
        这正是「防幻觉」最实际的一步：不是让模型承诺不说谎，而是让**依据可核查**。

        上下文里的编号 ``[1] [2]`` 与 ``sources`` 的顺序一一对应，
        prompt 中要求模型标注引用，用户就能逐条对照。
        """
        matches = await KnowledgeService._retrieve(db, keyword)

        for m in matches:
            await db.execute(
                update(KnowledgeBase).where(KnowledgeBase.id == m.id).values(
                    usage_count=KnowledgeBase.usage_count + 1
                )
            )
        await db.commit()

        if not matches:
            return "", []

        # sources 与上下文中的编号严格对应，供前端展示「依据」
        sources = [
            {
                "index": i + 1,
                "id": m.id,
                "question": m.question,
                "answer": (m.answer or "")[:200],
                "source": m.source,
                "quality_score": m.quality_score,
            }
            for i, m in enumerate(matches)
        ]

        context = (
            "以下是从知识库检索到的资料，请优先依据这些内容回答，"
            "并在引用处标注对应编号（如 [1]）：\n\n"
        )
        for item, m in zip(sources, matches):
            context += f"[{item['index']}] 问：{m.question}\n答：{m.answer}\n\n"

        return context, sources

    async def get_context_for_ai(db: AsyncSession, keyword: str) -> str:
        """检索知识库并返回注入用的上下文文本（不含来源）。

        保留这个入口是给「只需要上下文、不关心引用」的调用方用的
        （如知识库预览接口）；对话链路请用 ``get_context_with_sources``。
        """
        context, _sources = await KnowledgeService.get_context_with_sources(db, keyword)
        return context

    @staticmethod
    async def _retrieve(db: AsyncSession, keyword: str) -> list:
        """知识库检索入口：按配置策略执行，任一环节失败都降级为关键词检索。

        策略（RAG_RETRIEVAL_STRATEGY）：
        - auto      语义优先，无命中或异常时降级关键词（默认，保持服务不中断）
        - semantic  仅语义向量检索
        - keyword   仅关键词检索（无需任何密钥，可离线运行）
        - hybrid    向量 + 关键词的 RRF 融合

        语义检索能把「工资多少」和「薪资水平」这类表述不同但语义相近的问题
        关联起来，这是关键词匹配做不到的 —— 具体提升幅度见
        `scripts/eval_rag.py` 的对比评估结果。
        """
        strategy = (settings.rag_retrieval_strategy or "auto").strip().lower()

        if strategy == "keyword":
            return await KnowledgeService._keyword_search(db, keyword)

        if strategy == "hybrid":
            try:
                return await KnowledgeService._hybrid_search(db, keyword, top_k=5)
            except Exception as e:
                logger.warning(f"混合检索失败，降级关键词检索: {e}")
                return await KnowledgeService._keyword_search(db, keyword)

        # auto / semantic：优先向量检索
        if settings.embedding_enabled:
            try:
                semantic = await KnowledgeService._semantic_search(db, keyword, top_k=5)
                if semantic:
                    return semantic
            except Exception as e:
                logger.warning(f"语义检索失败，降级关键词检索: {e}")
        return await KnowledgeService._keyword_search(db, keyword)

    @staticmethod
    async def _semantic_search(db: AsyncSession, query: str, top_k: int = 5) -> list:
        """语义向量检索：查询与所有启用条目向量化后按余弦相似度排序取 Top-K。

        返回相似度不低于 SEMANTIC_THRESHOLD 的条目；若最高分仍低于阈值，视为
        无相关命中返回空列表（由 _retrieve 继续降级关键词）。
        """
        from app.services.embedding_service import embed_texts, cosine

        items = await KnowledgeService.get_all_enabled(db)
        if not items:
            return []

        doc_texts = [f"{it.question}\n{it.answer}\n{it.tags or ''}" for it in items]
        vectors = await embed_texts([query] + doc_texts)
        query_vec = vectors[0]
        doc_vecs = vectors[1:]

        scored = [
            (cosine(query_vec, vec), it)
            for it, vec in zip(items, doc_vecs)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored or scored[0][0] < SEMANTIC_THRESHOLD:
            return []
        return [it for sim, it in scored[:top_k] if sim >= SEMANTIC_THRESHOLD]

    @staticmethod
    async def _keyword_search(db: AsyncSession, keyword: str) -> list:
        """关键词检索（降级方案）：精确匹配优先，其次模糊匹配。"""
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.status == 1,
            KnowledgeBase.question == keyword
        ).limit(3)
        result = await db.execute(stmt)
        matches = result.scalars().all()

        if not matches:
            escaped = _escape_like(keyword)
            stmt = select(KnowledgeBase).where(
                KnowledgeBase.status == 1,
                or_(
                    KnowledgeBase.question.like(f"%{escaped}%", escape="\\"),
                    KnowledgeBase.tags.like(f"%{escaped}%", escape="\\"),
                    KnowledgeBase.answer.like(f"%{escaped}%", escape="\\")
                )
            ).order_by(KnowledgeBase.quality_score.desc()).limit(5)
            result = await db.execute(stmt)
            matches = result.scalars().all()

        return matches

    @staticmethod
    async def _hybrid_search(db: AsyncSession, query: str, top_k: int = 5) -> list:
        """向量检索 + 关键词检索的 RRF 融合。

        **为什么用 RRF（Reciprocal Rank Fusion）而不是加权求和**：
        余弦相似度的取值范围是 [-1, 1]，而关键词匹配没有可比的分数量纲，
        直接把两者加权相加需要人为调参且不稳定。RRF 只看**排名**不看分数：

            score(条目) = Σ 1 / (K + rank_第r路检索(条目))      K 取 60

        因而两路检索的分数尺度差异被彻底消除，且实现无需调参。
        K=60 是论文中的经验值，作用是压低头部排名的差异、避免某一路
        的单一结果独占权重。

        代价：RRF 至少需要两路都能返回结果才有融合效果；当向量化不可用时，
        本方法自动退化为纯关键词结果（由 _semantic_search 抛异常后返回空列表实现）。
        """
        rrf_k = 60
        try:
            semantic = await KnowledgeService._semantic_search(db, query, top_k=top_k * 2)
        except Exception as e:
            logger.warning(f"混合检索的向量分支失败，退化为关键词检索: {e}")
            semantic = []

        keyword = await KnowledgeService._keyword_search(db, query)

        if not semantic:
            return keyword[:top_k]
        if not keyword:
            return semantic[:top_k]

        fused: dict = {}
        for rank, item in enumerate(semantic, start=1):
            fused[item.id] = fused.get(item.id, 0.0) + 1.0 / (rrf_k + rank)
        for rank, item in enumerate(keyword, start=1):
            fused[item.id] = fused.get(item.id, 0.0) + 1.0 / (rrf_k + rank)

        by_id = {item.id: item for item in list(semantic) + list(keyword)}
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return [by_id[item_id] for item_id, _ in ranked[:top_k] if item_id in by_id]

    @staticmethod
    async def learn_from_response(
        db: AsyncSession, question: str, answer: str, tags: str = "", source: str = "auto"
    ):
        """把一次问答沉淀进知识库。

        **注意这是一条自我强化回路**：AI 自己生成的回答会被下一轮的语义检索
        当成「知识」召回并再次喂给模型。若模型某次答错，错误内容会被固化下来
        并不断复现。因此：
        - ``AI_AUTO_LEARN_ENABLED=false`` 可整体关闭自动入库；
        - 自动入库条目的 ``source`` 记录真实来源（模型名），便于与人工条目区分；
        - 检索时若需要只信人工内容，可按 ``source`` 过滤。

        ``source`` 原先被写死为 "zhipu"，在模型切换到 DeepSeek / 本地之后
        该标注已失真，且会让「自动入库量」的统计口径错位。
        """
        from app.config import settings

        if not getattr(settings, "ai_auto_learn_enabled", True):
            return
        # 质量门槛：答案过短视为低质量回答，不自动入库，避免污染知识库
        if not answer or len(answer.strip()) < 20:
            return

        stmt = select(KnowledgeBase).where(KnowledgeBase.question == question)
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return

        if not tags:
            tags = ",".join([kw for kw in KEYWORDS if kw in question or kw in answer])

        kb = KnowledgeBase(
            question=question,
            answer=answer,
            tags=tags,
            source=source or "auto",
            quality_score=1
        )
        db.add(kb)
        await db.commit()
        _invalidate_caches()

    @staticmethod
    async def get_stats(db: AsyncSession):
        total = await db.scalar(select(func.count()).select_from(KnowledgeBase))
        enabled = await db.scalar(select(func.count()).where(KnowledgeBase.status == 1))
        manual = await db.scalar(select(func.count()).where(KnowledgeBase.source == "manual"))
        # 自动入库 = 非人工录入。原先按 source == "zhipu" 统计，模型换成
        # DeepSeek / 本地之后这个口径就失效了（自动入库量恒为 0）。
        auto = await db.scalar(
            select(func.count()).where(
                (KnowledgeBase.source != "manual") | (KnowledgeBase.source.is_(None))
            )
        )
        return {"total": total, "enabled": enabled, "manual": manual, "auto": auto}