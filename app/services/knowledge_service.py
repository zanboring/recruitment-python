import time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, update

from app.models.knowledge_base import KnowledgeBase
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate

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
        _cache["data"] = None
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
        _cache["data"] = None
        return kb

    @staticmethod
    async def delete(db: AsyncSession, knowledge_id: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        await db.delete(kb)
        await db.commit()
        _cache["data"] = None

    @staticmethod
    async def toggle_status(db: AsyncSession, knowledge_id: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        kb.status = 1 if kb.status == 0 else 0
        await db.commit()
        _cache["data"] = None
        return kb

    @staticmethod
    async def set_score(db: AsyncSession, knowledge_id: int, score: int):
        kb = await KnowledgeService.get_by_id(db, knowledge_id)
        if not kb:
            raise ValueError("知识条目不存在")
        kb.quality_score = score
        await db.commit()
        _cache["data"] = None
        return kb

    @staticmethod
    async def get_context_for_ai(db: AsyncSession, keyword: str) -> str:
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

        for m in matches:
            await db.execute(
                update(KnowledgeBase).where(KnowledgeBase.id == m.id).values(
                    usage_count=KnowledgeBase.usage_count + 1
                )
            )
        await db.commit()

        if not matches:
            return ""
        context = "以下是相关知识库内容，请参考回答：\n\n"
        for m in matches:
            context += f"问：{m.question}\n答：{m.answer}\n\n"
        return context

    @staticmethod
    async def learn_from_response(db: AsyncSession, question: str, answer: str, tags: str = ""):
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
            source="zhipu",
            quality_score=1
        )
        db.add(kb)
        await db.commit()
        _cache["data"] = None

    @staticmethod
    async def get_stats(db: AsyncSession):
        total = await db.scalar(select(func.count()).select_from(KnowledgeBase))
        enabled = await db.scalar(select(func.count()).where(KnowledgeBase.status == 1))
        manual = await db.scalar(select(func.count()).where(KnowledgeBase.source == "manual"))
        auto = await db.scalar(select(func.count()).where(KnowledgeBase.source == "zhipu"))
        return {"total": total, "enabled": enabled, "manual": manual, "auto": auto}