"""知识库自动入库测试。

自动入库是一条**自我强化回路**：AI 的回答会被写进知识库，下一轮的语义检索
再把它当作「知识」召回并喂回模型。答错时错误会被固化并反复出现，因此需要
三个可控点：质量门槛、真实来源标注、总开关。本文件锁定这三件事。
"""
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.knowledge_base import KnowledgeBase
from app.services.knowledge_service import KnowledgeService

LONG_ANSWER = "这是一段足够长的回答内容，用于通过质量门槛的校验，确保能够正常入库。"


@pytest.fixture(autouse=True)
def _restore_auto_learn():
    original = settings.ai_auto_learn_enabled
    settings.ai_auto_learn_enabled = True
    yield
    settings.ai_auto_learn_enabled = original


@pytest.mark.asyncio
async def test_过短回答不入库(db_session):
    """质量门槛：长度不足视为低质量回答，避免脏数据回流。"""
    await KnowledgeService.learn_from_response(db_session, "问题", "太短")

    rows = (await db_session.execute(select(KnowledgeBase))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_空回答不入库(db_session):
    await KnowledgeService.learn_from_response(db_session, "问题", "")
    await KnowledgeService.learn_from_response(db_session, "问题", None)

    rows = (await db_session.execute(select(KnowledgeBase))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_同一问题不重复入库(db_session):
    await KnowledgeService.learn_from_response(db_session, "怎么用", LONG_ANSWER)
    await KnowledgeService.learn_from_response(db_session, "怎么用", LONG_ANSWER)

    rows = (await db_session.execute(select(KnowledgeBase))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_记录真实来源而不是写死的模型名(db_session):
    """回归：source 曾写死为 "zhipu"，模型换成 DeepSeek / 本地后标注就失真了。"""
    await KnowledgeService.learn_from_response(
        db_session, "长沙有什么岗位", LONG_ANSWER, source="deepseek"
    )

    row = (await db_session.execute(select(KnowledgeBase))).scalars().one()
    assert row.source == "deepseek"


@pytest.mark.asyncio
async def test_未指定来源时标记为auto(db_session):
    await KnowledgeService.learn_from_response(db_session, "问题一", LONG_ANSWER)

    row = (await db_session.execute(select(KnowledgeBase))).scalars().one()
    assert row.source == "auto"


@pytest.mark.asyncio
async def test_可整体关闭自动入库(db_session):
    """答错的内容不该被固化 —— 需要时能一键关掉这条回路。"""
    settings.ai_auto_learn_enabled = False

    await KnowledgeService.learn_from_response(
        db_session, "会答错的问题", LONG_ANSWER, source="deepseek"
    )

    rows = (await db_session.execute(select(KnowledgeBase))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_统计把非人工条目都算作自动入库(db_session):
    """口径不再绑定具体模型名，否则换模型后「自动入库量」会恒为 0。"""
    db_session.add_all([
        KnowledgeBase(question="人工问题", answer=LONG_ANSWER, source="manual", status=1),
        KnowledgeBase(question="模型问题A", answer=LONG_ANSWER, source="deepseek", status=1),
        KnowledgeBase(question="模型问题B", answer=LONG_ANSWER, source="ollama", status=1),
        # 不显式指定 source 时走列默认值 manual —— 这正是自动入库必须
        # 显式传 source 的原因：漏传会被误记成人工录入。
        KnowledgeBase(question="未标注来源", answer=LONG_ANSWER, status=0),
    ])
    await db_session.commit()

    stats = await KnowledgeService.get_stats(db_session)

    assert stats["total"] == 4
    assert stats["enabled"] == 3
    assert stats["manual"] == 2   # 显式 manual + 走默认值的那条
    assert stats["auto"] == 2     # deepseek + ollama


@pytest.mark.asyncio
async def test_自动入库必须显式传来源否则会被记为人工(db_session):
    """锁定上面那条隐患：learn_from_response 传了 source，不会踩默认值。"""
    await KnowledgeService.learn_from_response(
        db_session, "谁入库的", LONG_ANSWER, source="deepseek"
    )

    stats = await KnowledgeService.get_stats(db_session)
    assert stats["manual"] == 0
    assert stats["auto"] == 1
