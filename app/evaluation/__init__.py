"""RAG 检索效果评估模块。

组成：
- `golden_set`  —— 黄金测试集：仿真知识库 + 人工标注的查询→答案对
- `metrics`     —— Hit@K / MRR / Precision@K / Recall@K 纯函数实现
- `runner`      —— 在隔离内存库中对多种检索策略执行对比评估

入口脚本：`python scripts/eval_rag.py`
"""
from app.evaluation.golden_set import KNOWLEDGE_SEED, QUERIES
from app.evaluation.metrics import (
    evaluate_queries,
    hit_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.evaluation.runner import ALL_STRATEGIES, run_evaluation

__all__ = [
    "KNOWLEDGE_SEED",
    "QUERIES",
    "ALL_STRATEGIES",
    "run_evaluation",
    "evaluate_queries",
    "hit_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
