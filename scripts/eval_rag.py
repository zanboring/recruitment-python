"""RAG 检索效果评估入口。

用法：
    python scripts/eval_rag.py                          # 评估全部可用策略
    python scripts/eval_rag.py --strategies keyword     # 只跑关键词基线（离线可用）
    python scripts/eval_rag.py --output reports/rag_evaluation.md

说明：
- `semantic` / `hybrid` 策略需要 `ZHIPUAI_API_KEY`，未配置时会自动跳过并说明原因；
- 评估全程在内存库中进行，不影响项目数据库；
- 结果同时打印到控制台并写出 Markdown 报告，可直接用于项目文档或面试材料。
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# 让脚本可以直接以 `python scripts/eval_rag.py` 方式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，中文报告会乱码，这里强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.evaluation.runner import ALL_STRATEGIES, run_evaluation  # noqa: E402

STRATEGY_LABELS = {
    "keyword": "关键词检索（LIKE 子串匹配）",
    "semantic": "语义向量检索（embedding 余弦相似度）",
    "hybrid": "混合检索（向量 + 关键词 RRF 融合）",
}

CATEGORY_LABELS = {
    "literal": "字面型查询",
    "semantic": "语义型查询",
}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_console(payload: dict) -> None:
    results = payload["results"]
    print("=" * 78)
    print(f"RAG 检索效果评估    知识条目 {payload['knowledge_count']} 条 / "
          f"查询 {payload['query_count']} 条 / Top-{payload['top_k']}")
    print("=" * 78)

    header = f"{'策略':<12}{'Hit@1':>9}{'Hit@3':>9}{'Hit@5':>9}{'MRR':>8}{'P@5':>8}{'R@5':>8}"
    print(header)
    print("-" * 78)
    for name, m in results.items():
        if not m.get("count"):
            continue
        print(f"{name:<12}{_pct(m['hit@1']):>9}{_pct(m['hit@3']):>9}"
              f"{_pct(m['hit@5']):>9}{m['mrr']:>8.3f}"
              f"{_pct(m['precision@5']):>8}{_pct(m['recall@5']):>8}")

    print("\n按查询类型分组（Hit@5 / MRR）：")
    for name, m in results.items():
        if not m.get("count"):
            continue
        line = f"  {name:<12}"
        for cat, cm in m.get("by_category", {}).items():
            line += f"  {CATEGORY_LABELS.get(cat, cat)}(n={cm['count']}): " \
                    f"Hit@5={_pct(cm['hit@5'])} MRR={cm['mrr']:.3f}"
        print(line)

    if payload["skipped"]:
        print("\n跳过的策略：")
        for name, reason in payload["skipped"].items():
            print(f"  {name}: {reason}")
    print("=" * 78)


def render_markdown(payload: dict) -> str:
    results = payload["results"]
    lines = [
        "# RAG 检索效果评估报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　|　"
        f"知识条目 **{payload['knowledge_count']}** 条　|　"
        f"查询 **{payload['query_count']}** 条　|　截断 **Top-{payload['top_k']}**",
        "",
        "## 一、评估方法",
        "",
        "在独立的内存数据库中灌入仿真知识库，对同一批人工标注的查询分别执行三种检索策略，",
        "计算 Hit@K、MRR、Precision@K、Recall@K。查询分为两类：",
        "",
        "- **字面型**（literal）：与知识条目用词重合，关键词子串匹配即可命中；",
        "- **语义型**（semantic）：口语化改写，字面几乎不重合（如问「工资」而条目写「薪资」），",
        "  只有向量检索才可能命中。",
        "",
        "分类统计的意义在于：如果只看总分，会掩盖「语义检索在改写查询上的提升」这一核心结论。",
        "",
        "## 二、主结果",
        "",
        "| 检索策略 | Hit@1 | Hit@3 | Hit@5 | MRR | Precision@5 | Recall@5 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, m in results.items():
        if not m.get("count"):
            continue
        lines.append(
            f"| {STRATEGY_LABELS.get(name, name)} | {_pct(m['hit@1'])} | {_pct(m['hit@3'])} | "
            f"{_pct(m['hit@5'])} | {m['mrr']:.3f} | {_pct(m['precision@5'])} | {_pct(m['recall@5'])} |"
        )

    lines += ["", "## 三、分类别结果（核心结论）", "",
              "| 检索策略 | 字面型 Hit@5 | 字面型 MRR | 语义型 Hit@5 | 语义型 MRR |",
              "|---|---|---|---|---|"]
    for name, m in results.items():
        cats = m.get("by_category", {})
        lit, sem = cats.get("literal", {}), cats.get("semantic", {})
        lines.append(
            f"| {name} | {_pct(lit.get('hit@5', 0))} | {lit.get('mrr', 0):.3f} | "
            f"{_pct(sem.get('hit@5', 0))} | {sem.get('mrr', 0):.3f} |"
        )

    if payload["skipped"]:
        lines += ["", "## 四、未执行的策略", ""]
        for name, reason in payload["skipped"].items():
            lines.append(f"- `{name}`：{reason}")

    lines += [
        "",
        "## 五、指标说明",
        "",
        "| 指标 | 含义 |",
        "|---|---|",
        "| Hit@K | Top-K 结果中是否出现至少一条正确答案，衡量「找得到吗」 |",
        "| MRR | 首个正确答案排名的倒数均值，衡量「排得够不够前」 |",
        "| Precision@K | Top-K 中正确答案占比，衡量「结果干不干净」 |",
        "| Recall@K | Top-K 覆盖标准答案的比例，衡量「找得全不全」 |",
        "",
        "> 复现方式：`python scripts/eval_rag.py`",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索效果评估")
    parser.add_argument("--strategies", default=",".join(ALL_STRATEGIES),
                        help="逗号分隔的策略列表，可选 keyword/semantic/hybrid")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K 截断，默认 5")
    parser.add_argument("--embed-backend", choices=["zhipu", "ollama"],
                        help="覆盖向量化后端：zhipu 云端 / ollama 本地")
    parser.add_argument("--output", help="Markdown 报告输出路径")
    args = parser.parse_args()

    if args.embed_backend:
        from app.config import settings
        settings.embedding_backend = args.embed_backend
        print(f"[配置] 本次评估使用向量化后端：{args.embed_backend}\n")

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    payload = await run_evaluation(strategies=strategies, top_k=args.top_k)

    render_console(payload)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(payload), encoding="utf-8")
        print(f"\nMarkdown 报告已写入：{out}")


if __name__ == "__main__":
    asyncio.run(main())
