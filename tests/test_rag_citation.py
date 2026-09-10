"""RAG 引用溯源测试。

动机：把资料塞进 prompt 再写一句「请参考回答」，对使用者完全是不透明的 ——
他不知道模型是查到了资料还是凭空作答。返回来源后，「依据」变得可核查，
这是「防幻觉」里最实际的一步：不是让模型承诺不说谎，而是**让依据可验证**。

本文件锁定三件事：
1. 上下文里的编号与 sources 顺序严格一一对应（错位会让引用张冠李戴）；
2. 没有命中时返回空上下文 + 空来源（不能编造依据）；
3. 来源回调即使抛异常也不能影响对话本身。
"""
import pytest
from sqlalchemy import select

from app.models.knowledge_base import KnowledgeBase
from app.services import ai_service
from app.services.knowledge_service import KnowledgeService


@pytest.fixture
async def kb(db_session):
    db_session.add_all([
        KnowledgeBase(
            question="招聘岗位的薪资水平如何确定",
            answer="薪资结合城市系数、经验、学历与技能溢价综合测算。",
            tags="薪资,工资", source="manual", status=1, quality_score=5,
        ),
        KnowledgeBase(
            question="怎么使用岗位推荐功能",
            answer="在推荐页填写技能、学历与经验年限即可获得匹配结果。",
            tags="推荐,使用", source="manual", status=1, quality_score=3,
        ),
        KnowledgeBase(
            question="已下架的问题",
            answer="这条不应被检索到，因为状态是禁用。",
            tags="薪资", source="manual", status=0, quality_score=1,
        ),
    ])
    await db_session.commit()
    return db_session


# ---------- 上下文与来源的对应关系 ----------

@pytest.mark.asyncio
async def test_返回上下文与来源且编号一一对应(kb):
    context, sources = await KnowledgeService.get_context_with_sources(kb, "薪资")

    assert sources, "应至少命中一条"
    assert context

    # 编号从 1 开始且连续
    for i, item in enumerate(sources, start=1):
        assert item["index"] == i
        # 上下文里必须出现同一编号的标记
        assert f"[{i}]" in context

    # 每个来源都带够前端展示所需字段
    first = sources[0]
    assert first["id"] and first["question"] and "source" in first


@pytest.mark.asyncio
async def test_来源顺序与上下文出现顺序一致(kb):
    context, sources = await KnowledgeService.get_context_with_sources(kb, "薪资")

    positions = [context.index(f"[{item['index']}]") for item in sources]
    assert positions == sorted(positions), "上下文中的编号顺序应与 sources 顺序一致"


@pytest.mark.asyncio
async def test_上下文包含引用标注要求(kb):
    """prompt 里必须要求模型标注引用，否则来源只是摆设。"""
    context, _ = await KnowledgeService.get_context_with_sources(kb, "薪资")
    assert "编号" in context or "[1]" in context


@pytest.mark.asyncio
async def test_禁用条目不会被检索到(kb):
    _context, sources = await KnowledgeService.get_context_with_sources(kb, "薪资")
    assert all("已下架" not in s["question"] for s in sources)


@pytest.mark.asyncio
async def test_没有命中时返回空上下文与空来源(db_session):
    """不能在没有命中时编造依据。"""
    context, sources = await KnowledgeService.get_context_with_sources(
        db_session, "完全不相关的查询词xyz"
    )
    assert context == ""
    assert sources == []


@pytest.mark.asyncio
async def test_命中条目的使用次数会累加(kb):
    await KnowledgeService.get_context_with_sources(kb, "薪资")

    rows = (await kb.execute(select(KnowledgeBase))).scalars().all()
    hit = [r for r in rows if r.tags and "薪资" in r.tags and r.status == 1]
    assert all(r.usage_count and r.usage_count >= 1 for r in hit)


# ---------- 向后兼容 ----------

@pytest.mark.asyncio
async def test_原有接口仍只返回文本(kb):
    """get_context_for_ai 保持原契约，避免影响只关心上下文的调用方。"""
    context = await KnowledgeService.get_context_for_ai(kb, "薪资")

    assert isinstance(context, str)
    assert context          # 有命中时应有内容


# ---------- 回调转发 ----------

@pytest.mark.asyncio
async def test_构建上下文时转发来源给回调(kb):
    captured = []

    await ai_service.build_rag_context(kb, "薪资", on_sources=captured.extend)

    assert captured
    assert captured[0]["index"] == 1


@pytest.mark.asyncio
async def test_没有数据库时不回调也不报错():
    context = await ai_service.build_rag_context(None, "薪资", on_sources=lambda s: None)
    assert context == ""


@pytest.mark.asyncio
async def test_来源回调抛异常不影响上下文构建(kb):
    """观测（回调）失败绝不能反过来影响被观测的行为。"""

    def boom(_items):
        raise RuntimeError("前端回调炸了")

    context = await ai_service.build_rag_context(kb, "薪资", on_sources=boom)

    assert context, "回调异常时仍应返回可用的上下文"


@pytest.mark.asyncio
async def test_降级链不会重复检索知识库(db_session, monkeypatch):
    """知识库检索必须在降级链之外只发生一次。

    原实现把检索放在每一级的内部：云端失败后重试本地会再检索一遍。实测一次提问
    触发了 **3 次**检索，后果有两个，都属于「看不出来但确实是错的」：

    1. ``usage_count`` 被虚增 3 倍 —— 该字段参与知识条目的质量排序，属数据污染；
    2. ``on_sources`` 被回调 3 次 —— 前端会展示 3 条完全相同的「引用来源」。

    检索结果与「最终用哪个模型回答」无关，本就应该只算一次。
    """
    from app.config import settings
    from app.services.llm_client import CloudProvider

    db_session.add(KnowledgeBase(
        question="薪资水平如何",
        answer="薪资水平由城市、经验与技能共同决定，具体可在岗位列表中查看。",
        source="manual", status=1
    ))
    await db_session.commit()

    # 构造两个云端 provider 并让它们全部失败，再关掉本地模型 ——
    # 强制走完「云端 → 本地 → 规则引擎」整条链，覆盖最多重试次数的情况
    fake_providers = [
        CloudProvider(name="A", provider="a", api_url="http://x", api_key="k", model="m"),
        CloudProvider(name="B", provider="b", api_url="http://x", api_key="k", model="m"),
    ]
    monkeypatch.setattr(
        "app.services.llm_client.get_cloud_providers", lambda *a, **k: fake_providers
    )

    async def _cloud_down(*args, **kwargs):
        raise RuntimeError("云端不可用")
        yield  # noqa: 使其成为异步生成器

    monkeypatch.setattr("app.services.llm_client.stream_chat", _cloud_down)
    monkeypatch.setattr(settings, "ollama_enabled", False)

    received: list = []
    async for _ in ai_service.call_chat_stream(
        "薪资水平如何", "rag-once-1", db_session, None,
        on_sources=lambda items: received.append(list(items)),
    ):
        pass

    # 来源回调只发生一次，且内容不重复
    assert len(received) == 1
    assert len(received[0]) == 1

    # 关键：usage_count 只加了 1，而不是每重试一级就加一次
    row = (await db_session.execute(select(KnowledgeBase))).scalars().one()
    assert row.usage_count == 1


@pytest.mark.asyncio
async def test_未检索时各层仍可独立工作(kb, monkeypatch):
    """rag_context 为 None 表示调用方没检索 —— 函数自行检索，保持可独立调用。

    这条保证「上提检索」没有破坏 call_cloud_stream / call_ollama_stream
    作为公开函数的可用性（例如分析报告走的是独立调用）。
    """
    captured: list = []
    monkeypatch.setattr("app.services.llm_client.stream_chat", _fake_stream)

    chunks = []
    async for chunk in ai_service.call_cloud_stream(
        None, "薪资水平如何", "s1", kb, None,
        on_sources=lambda items: captured.append(list(items)),
    ):
        chunks.append(chunk)

    # 未传 rag_context → 内部自行检索一次，来源照常透出
    assert captured and captured[0]
    assert "".join(chunks) == "薪资水平如何"


async def _fake_stream(*args, **kwargs):
    """固定内容的流式桩：让「来源透出」的验证不依赖真实模型调用。"""
    for piece in ("薪资", "水平", "如何"):
        yield piece
