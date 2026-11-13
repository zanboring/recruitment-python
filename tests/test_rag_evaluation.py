"""RAG 检索评估模块测试。

分三层：
1. **指标计算的正确性**（纯函数，覆盖边界）—— 指标算错，整个评估结论就全是错的，
   这是评估体系可信度的地基；
2. **数据集自洽性** —— 校验每个查询都标注了真实存在的标准答案；
3. **评估流程与数据集区分度** —— 特别验证「语义型查询确实是关键词检索的盲区」，
   若这条不成立，说明数据集无法区分不同检索策略，评估就失去了意义。
"""
import importlib.util
import re
from pathlib import Path

import pytest

from app.evaluation.golden_set import KNOWLEDGE_SEED, QUERIES
from app.evaluation.metrics import (
    evaluate_queries,
    hit_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.evaluation.runner import run_evaluation

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "docs" / "reports"


def _load_eval_rag():
    """按路径加载 `scripts/eval_rag.py`（`scripts/` 不是包，不能直接 import）。"""
    script = REPO_ROOT / "scripts" / "eval_rag.py"
    spec = importlib.util.spec_from_file_location("_eval_rag_report", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_payload(skipped=None) -> dict:
    """给 render_markdown 的最小完整输入（不跑任何检索）。"""
    metrics = {
        "count": 28,
        "hit@1": 0.5, "hit@3": 0.6, "hit@5": 0.7,
        "mrr": 0.5, "precision@5": 0.1, "recall@5": 0.7,
        "by_category": {
            "literal": {"count": 12, "hit@5": 1.0, "mrr": 0.9},
            "semantic": {"count": 16, "hit@5": 0.4, "mrr": 0.3},
        },
    }
    return {
        "knowledge_count": 20,
        "query_count": 28,
        "top_k": 5,
        "results": {"keyword": metrics},
        "skipped": skipped or {},
    }


class TestMetrics:
    def test_命中判定只看_top_k_范围(self):
        assert hit_at_k([1, 2, 3], {3}, 3) == 1.0
        assert hit_at_k([1, 2, 3], {3}, 2) == 0.0, "第 3 位不该算进 Top-2"

    def test_没有标准答案时命中为零(self):
        assert hit_at_k([1, 2], set(), 5) == 0.0
        assert reciprocal_rank([1, 2], set()) == 0.0

    def test_排序越靠前倒数排名分越高(self):
        assert reciprocal_rank([9, 8, 7], {9}) == 1.0
        assert reciprocal_rank([9, 8, 7], {8}) == 0.5
        assert reciprocal_rank([9, 8, 7], {7}) == pytest.approx(1 / 3)

    def test_完全未命中时倒数排名为零(self):
        assert reciprocal_rank([9, 8], {1}) == 0.0

    def test_精确率与召回率(self):
        assert precision_at_k([1, 2, 3, 4], {1, 3}, 4) == 0.5
        assert recall_at_k([1, 2, 3, 4], {1, 3, 9}, 4) == pytest.approx(2 / 3)

    def test_汇总包含全部指标与分组结果(self):
        records = [([1, 2], {1}, "literal"), ([3, 4], {9}, "semantic")]
        metrics = evaluate_queries(records, ks=(1, 3, 5))

        assert metrics["count"] == 2
        assert metrics["hit@1"] == 0.5
        assert metrics["by_category"]["literal"]["hit@1"] == 1.0
        assert metrics["by_category"]["semantic"]["hit@1"] == 0.0

    def test_空输入不抛异常(self):
        assert evaluate_queries([]) == {"count": 0}


class TestGoldenSet:
    def test_每个查询都标注了真实存在的标准答案(self):
        questions = {item["question"] for item in KNOWLEDGE_SEED}
        for query in QUERIES:
            assert query["targets"], f"查询 {query['query']!r} 未标注标准答案"
            for target in query["targets"]:
                assert target in questions, f"目标不在知识库中：{target}"

    def test_两类查询都有足够样本(self):
        """字面型与语义型都需要有样本量，否则分组结论不具统计意义。"""
        categories = {q["category"] for q in QUERIES}
        assert categories == {"literal", "semantic"}
        for category in categories:
            count = sum(1 for q in QUERIES if q["category"] == category)
            assert count >= 8, f"{category} 类样本过少（{count} 条）"

    def test_知识库种子字段完整(self):
        for item in KNOWLEDGE_SEED:
            assert item["question"] and item["answer"] and item["tags"]


@pytest.mark.asyncio
class TestEvaluationFlow:
    async def test_关键词策略可离线跑通(self):
        """关键词检索不依赖任何密钥，必须能在离线环境给出完整指标。"""
        payload = await run_evaluation(strategies=["keyword"], top_k=5)
        metrics = payload["results"]["keyword"]

        assert metrics["count"] == len(QUERIES)
        assert 0.0 <= metrics["hit@5"] <= 1.0
        assert 0.0 <= metrics["mrr"] <= 1.0

    async def test_语义型是关键词检索的盲区(self):
        """数据集区分度自检。

        这是整套评估的意义所在：只有当「语义型查询在关键词检索下几乎不命中」时，
        才能用它来量化语义检索带来的提升。若这条断言失败，说明数据集设计有问题。
        """
        payload = await run_evaluation(strategies=["keyword"], top_k=5)
        by_category = payload["results"]["keyword"]["by_category"]

        assert by_category["literal"]["hit@5"] > by_category["semantic"]["hit@5"], (
            "字面型命中率应显著高于语义型，否则数据集无法区分检索策略"
        )

    async def test_评估结果结构完整(self):
        payload = await run_evaluation(strategies=["keyword"], top_k=5)
        for field in ("top_k", "query_count", "knowledge_count", "results", "skipped"):
            assert field in payload
        assert payload["knowledge_count"] == len(KNOWLEDGE_SEED)

    async def test_向量化不可用时跳过并说明原因(self, monkeypatch):
        """跳过必须带原因 —— 否则会被误读成「语义检索效果差」。"""
        import app.evaluation.runner as runner

        async def _probe_fail():
            return False, "向量化后端不可用：模拟失败"

        monkeypatch.setattr(runner, "_probe_embedding", _probe_fail)

        payload = await runner.run_evaluation(strategies=["semantic"], top_k=5)
        assert "semantic" in payload["skipped"]
        assert "向量化后端不可用" in payload["skipped"]["semantic"]
        assert "semantic" not in payload["results"]


@pytest.mark.asyncio
class TestHybridSearch:
    async def test_无向量化时退化为关键词检索(self, db_session):
        """混合检索的向量分支不可用时，应优雅退化而不是整体失败。"""
        from app.models.knowledge_base import KnowledgeBase
        from app.services.knowledge_service import KnowledgeService

        db_session.add(KnowledgeBase(
            question="测试条目", answer="测试答案", tags="测试", status=1, quality_score=1
        ))
        await db_session.commit()

        items = await KnowledgeService._hybrid_search(db_session, "测试条目", top_k=5)
        assert items, "退化后应返回关键词检索结果"
        assert items[0].question == "测试条目"


class TestReportStructure:
    """报告结构回归：整页覆写不能冲掉人工补记，章节编号不能跳号。"""

    _SECTION_RE = re.compile(r"^## ([一二三四五六七八九十])、", re.MULTILINE)

    def test_章节编号连续(self):
        """无跳过策略时是 一/二/三/四，不能出现「三 → 五」。"""
        mod = _load_eval_rag()
        text = mod.render_markdown(_sample_payload())

        assert self._SECTION_RE.findall(text) == ["一", "二", "三", "四"]

    def test_有跳过策略时编号仍连续(self):
        """插入「未执行的策略」一节后，后续章节要顺延，而不是仍写死「五」。"""
        mod = _load_eval_rag()
        text = mod.render_markdown(_sample_payload({"semantic": "未配置向量化后端"}))

        assert self._SECTION_RE.findall(text) == ["一", "二", "三", "四", "五"]

    def test_重跑写入流程不会改动历史对照文件(self, tmp_path):
        """用一份「带历史小节」的报告跑一次写入流程，断言补记原样还在。

        采用方案 A：主报告整页覆写，历史对照另存独立文件，脚本不读也不写它。
        这里把历史文件放在同一目录，模拟真实的 docs/reports/ 结构。
        """
        mod = _load_eval_rag()
        history = tmp_path / "rag-evaluation-history.md"
        history.write_text(
            "# 历史对照\n语义型 Hit@5 43.8% → 93.8%\n混合检索 67.9% → 96.4%\n",
            encoding="utf-8",
        )
        before = history.read_bytes()

        main = tmp_path / "rag-evaluation.md"
        main.write_text("上一轮生成物", encoding="utf-8")

        mod.write_report(_sample_payload(), main)

        assert history.read_bytes() == before, "历史对照文件被写入了 —— 补记会丢"
        rendered = main.read_text(encoding="utf-8")
        assert "上一轮生成物" not in rendered, "主报告应被整页覆写"
        assert mod.HISTORY_REPORT_FILENAME in rendered, "主报告必须指回历史对照文件"

    def test_仓库内主报告与历史对照已拆分(self):
        """真实文件层面的契约：数字只在历史文件维护，主报告只留链接。"""
        report = (REPORTS_DIR / "rag-evaluation.md").read_text(encoding="utf-8")
        history = (REPORTS_DIR / "rag-evaluation-history.md").read_text(encoding="utf-8")

        assert "rag-evaluation-history.md" in report
        assert "93.8%" in history and "96.4%" in history
        assert "93.8%" not in report, "人工补记的数字不应再写进会被覆写的主报告"
