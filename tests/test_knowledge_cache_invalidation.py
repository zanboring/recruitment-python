"""知识库缓存失效测试 —— 锁住「漏 await」这个真实踩过的坑。

背景（这个 bug 曾在本仓库真实存在，且极难察觉）：
``KnowledgeService._invalidate_caches`` 是 ``async`` 函数，但
``update`` / ``delete``、兼容路由 ``compat.py``、评测 ``runner.py``
四处调用时漏写了 ``await`` —— 协程对象被创建后**从未执行**，
缓存实际上从没被清过。

表现：知识条目的内容改了，检索到的却还是旧内容，最长
``CACHE_TTL``（600 秒）内不生效，**且不抛异常、不报错**
（Python 只在 GC 时抛 ``RuntimeWarning``，而 pytest 默认不会因此失败，
``pytest.ini`` 的 ``filterwarnings`` 也拦不住）。

本文件用两道防线锁住它：

1. **行为测试** —— 预置缓存后调用各变更入口，断言缓存真的被清空；
2. **AST 静态守卫** —— 扫描 ``app/`` 下全部 ``_invalidate_caches()``
   调用点，任何漏写 ``await`` 的写法直接判失败。这一道能覆盖
   「单元测试跑不到」的代码路径（如评测 runner），是行为测试的补充。
"""
import ast
import pathlib

import pytest

from app.cache import cache
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.knowledge_service import CACHE_KEY_ENABLED_IDS, KnowledgeService

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
_TARGET = "_invalidate_caches"


@pytest.fixture(autouse=True)
def _force_memory_cache():
    """测试不依赖真实 Redis：显式降级到内存后端，避免连接探测造成抖动。"""
    cache._redis_broken = True
    yield


async def _seed_cache() -> None:
    """预置一条 ``kb:`` 前缀缓存，模拟「检索已经发生过」的现场。"""
    await cache.set(CACHE_KEY_ENABLED_IDS, "1,2,3", 600)
    assert await cache.get(CACHE_KEY_ENABLED_IDS) is not None


async def _assert_cache_cleared() -> None:
    assert await cache.get(CACHE_KEY_ENABLED_IDS) is None, "变更后 kb: 缓存未被清除"


# --------------------------------------------------------------------------
# 防线 1：行为测试
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_创建后清空缓存(db_session):
    await _seed_cache()
    await KnowledgeService.create(
        db_session, KnowledgeBaseCreate(question="新问题", answer="新答案")
    )
    await _assert_cache_cleared()


@pytest.mark.asyncio
async def test_更新后清空缓存(db_session):
    kb = await KnowledgeService.create(
        db_session, KnowledgeBaseCreate(question="原问题", answer="原答案")
    )
    # create 自身也会清缓存，这里重新种一条，
    # 保证下面的断言只反映 update 的行为。
    await _seed_cache()

    await KnowledgeService.update(
        db_session, kb.id, KnowledgeBaseUpdate(question="改过的问题")
    )
    await _assert_cache_cleared()


@pytest.mark.asyncio
async def test_删除后清空缓存(db_session):
    kb = await KnowledgeService.create(
        db_session, KnowledgeBaseCreate(question="待删除", answer="答案")
    )
    await _seed_cache()

    await KnowledgeService.delete(db_session, kb.id)
    await _assert_cache_cleared()


@pytest.mark.asyncio
async def test_切换状态后清空缓存(db_session):
    kb = await KnowledgeService.create(
        db_session, KnowledgeBaseCreate(question="切换状态", answer="答案")
    )
    await _seed_cache()

    await KnowledgeService.toggle_status(db_session, kb.id)
    await _assert_cache_cleared()


@pytest.mark.asyncio
async def test_设置评分后清空缓存(db_session):
    kb = await KnowledgeService.create(
        db_session, KnowledgeBaseCreate(question="设置评分", answer="答案")
    )
    await _seed_cache()

    await KnowledgeService.set_score(db_session, kb.id, 5)
    await _assert_cache_cleared()


# --------------------------------------------------------------------------
# 防线 2：AST 静态守卫（覆盖单测跑不到的路径）
# --------------------------------------------------------------------------

def _scan_file(path: pathlib.Path) -> tuple[list[ast.Call], set[int]]:
    """返回 (所有 _invalidate_caches 调用节点, 已被 await 的调用节点 id 集合)。"""
    # 用 utf-8-sig 读取：即使文件带 BOM 也能正常解析。
    # BOM 本身由 tests/test_source_hygiene.py 单独守卫，这里不重复报错。
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    def call_name(node: ast.AST) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        return getattr(func, "attr", None) or getattr(func, "id", None)

    awaited: set[int] = set()
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and call_name(node.value) == _TARGET:
            awaited.add(id(node.value))
        if call_name(node) == _TARGET:
            calls.append(node)
    return calls, awaited


def test_所有_invalidate_caches调用都带await():
    """静态守卫：任何漏写 await 的调用点都会让本用例失败。

    覆盖 ``compat.py`` / ``runner.py`` 这类单元测试不易触达的路径。
    """
    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        calls, awaited = _scan_file(path)
        for call in calls:
            if id(call) not in awaited:
                rel = path.relative_to(APP_DIR.parent)
                violations.append(f"  {rel}:{call.lineno}")

    assert not violations, (
        "以下 _invalidate_caches() 调用缺少 await —— "
        "协程不会执行，缓存不会被清：\n" + "\n".join(violations)
    )
