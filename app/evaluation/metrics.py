"""RAG 检索质量评估指标（纯函数实现，不引入第三方评估库）。

**为什么自研而不用 ragas / deepeval**：
- 本项目只需评估「内容检索（retrieval）」层面，不涉及 LLM 裁判，因而零成本、零额外依赖；
- 指标定义与计算过程完全透明 —— 面试时能逐行说明「命中」与「排序」是怎么算出来的，
  而不是调一个库得到黑盒分数。

**术语约定**：
    retrieved   检索器返回的条目 ID 列表，**按相关性降序**（检索器自己的输出顺序）
    relevant    该查询的标准答案条目 ID 集合（黄金测试集中人工标注）
    k           Top-K 截断位置

**指标含义**：
    Hit@K         Top-K 里是否出现了至少一条正确答案 —— 衡量"找得到吗"
    MRR           首个正确答案排名的倒数均值 —— 衡量"排得够不够前"
    Precision@K   Top-K 中正确答案占比 —— 衡量"结果有多干净"
    Recall@K      Top-K 覆盖了多少比例的正确答案 —— 衡量"找得全不全"
"""


def hit_at_k(retrieved: list, relevant, k: int) -> float:
    """Top-K 中是否命中至少一条正确答案。返回 1.0 / 0.0。"""
    if not relevant:
        return 0.0
    return 1.0 if set(retrieved[:k]) & set(relevant) else 0.0


def reciprocal_rank(retrieved: list, relevant) -> float:
    """首个正确答案的排名倒数（1/rank）；未命中返回 0。

    注意：只看**第一个**正确答案的位置 —— 把答案排在第 1 位得 1.0，
    排在第 5 位只得 0.2，这个指标专门惩罚"答案在但埋得深"。
    """
    relevant = set(relevant)
    if not relevant:
        return 0.0
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def precision_at_k(retrieved: list, relevant, k: int) -> float:
    """Top-K 中正确答案所占比例。"""
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    return len(set(top_k) & set(relevant)) / len(top_k)


def recall_at_k(retrieved: list, relevant, k: int) -> float:
    """Top-K 覆盖了标准答案集合的多大比例。"""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_queries(records: list, ks=(1, 3, 5)) -> dict:
    """汇总一批查询的指标。

    参数：
        records  list[(retrieved_ids, relevant_ids, category)]
                 category 用于分组统计，例如按查询难度/类型拆开看
        ks       需要统计的 Top-K 截断位置

    返回：
        {
          "count": 样本数,
          "hit@1": .., "hit@3": .., "hit@5": ..,
          "mrr": .., "precision@5": .., "recall@5": ..,
          "by_category": { 类别: {同类指标} }
        }
    """
    if not records:
        return {"count": 0}

    overall = {
        "count": len(records),
        "mrr": _mean([reciprocal_rank(r, rel) for r, rel, _ in records]),
        "precision@5": _mean([precision_at_k(r, rel, 5) for r, rel, _ in records]),
        "recall@5": _mean([recall_at_k(r, rel, 5) for r, rel, _ in records]),
    }
    for k in ks:
        overall[f"hit@{k}"] = _mean([hit_at_k(r, rel, k) for r, rel, _ in records])

    # 分组统计：只看总分会掩盖「某类查询特别差」的情况
    categories: dict = {}
    for retrieved, relevant, category in records:
        categories.setdefault(category, []).append((retrieved, relevant, category))
    overall["by_category"] = {
        name: {
            "count": len(group),
            "mrr": _mean([reciprocal_rank(r, rel) for r, rel, _ in group]),
            **{f"hit@{k}": _mean([hit_at_k(r, rel, k) for r, rel, _ in group]) for k in ks},
        }
        for name, group in categories.items()
    }
    return overall
