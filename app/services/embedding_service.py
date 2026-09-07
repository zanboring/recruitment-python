"""向量语义检索服务：知识库 RAG 的 embedding 与相似度计算。

设计说明（为什么不用本地 bge + Chroma）：
- 知识库规模小（FAQ 级，几十到几百条），用云端 embedding API + 纯 Python 余弦
  相似度即可满足语义召回，无需引入 sentence-transformers（依赖 torch，体积大、
  冷启动慢）或单独部署向量数据库；
- 复用系统已有的 ZHIPUAI_API_KEY，不新增任何第三方依赖（仅 httpx，requirements 已有）；
- 向量化失败（无 key / 网络不通 / 接口报错）时抛异常，由调用方（knowledge_service）
  自动降级回关键词检索，保证服务不中断 —— 与系统「三级降级」思想一致。

更大规模、需要重排序（RRF）的向量检索见独立项目 rag-qa-system（ChromaDB + RRF）。
"""
import hashlib
import logging
import math
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# embedding-3 单次请求最多 64 条文本
_CHUNK_SIZE = 64

# 单条文本 embedding 的内存缓存：text_hash -> (embedding, timestamp)
_embedding_memo: dict = {}
_MEMO_TTL = 3600  # 单条向量缓存 1 小时，知识库变更时由 invalidate 主动失效


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def invalidate_embedding_cache() -> None:
    """知识库内容变更（新增/修改/删除/学习入库）时调用，清空向量缓存。"""
    _embedding_memo.clear()


def cosine(a: list, b: list) -> float:
    """余弦相似度（纯 Python 实现，避免引入 numpy 依赖）。

    两个向量逐元素点积除以各自模长，取值 [-1, 1]，越接近 1 越相似。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _embed_batch(texts: list) -> list:
    """调用智谱 embedding API 批量向量化（一次请求，不超过 _CHUNK_SIZE 条）。"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.zhipuai_api_key}",
    }
    payload = {
        "model": settings.embedding_model,
        "input": texts,
        "dimensions": settings.embedding_dimensions,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.embedding_api_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    # 按 index 排序，保证返回顺序与输入顺序一致
    items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in items]


async def embed_texts(texts: list) -> list:
    """批量文本向量化，返回与输入顺序一致的向量列表。

    - 命中内存缓存的文本直接复用，不再请求 API；
    - 新文本按 _CHUNK_SIZE 分批调用 embedding API；
    - 未配置 key 或接口失败时抛异常，由调用方降级。
    """
    if not texts:
        return []
    if not settings.zhipuai_api_key:
        raise RuntimeError("未配置 ZHIPUAI_API_KEY，无法进行语义向量化")

    now = time.time()
    results: list = [None] * len(texts)
    pending_idx: list = []

    for i, text in enumerate(texts):
        key = _text_key(text)
        hit = _embedding_memo.get(key)
        if hit is not None and now - hit[1] < _MEMO_TTL:
            results[i] = hit[0]
        else:
            pending_idx.append(i)

    for start in range(0, len(pending_idx), _CHUNK_SIZE):
        chunk_idx = pending_idx[start:start + _CHUNK_SIZE]
        chunk_texts = [texts[i] for i in chunk_idx]
        vectors = await _embed_batch(chunk_texts)
        for i, vec in zip(chunk_idx, vectors):
            _embedding_memo[_text_key(texts[i])] = (vec, now)
            results[i] = vec

    return results
