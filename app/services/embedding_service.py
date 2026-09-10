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


async def _embed_batch_zhipu(texts: list) -> list:
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
    await _record_embedding_usage(
        "zhipu", settings.embedding_model, len(texts), data.get("usage")
    )
    # 按 index 排序，保证返回顺序与输入顺序一致
    items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in items]


async def _embed_batch_ollama(texts: list) -> list:
    """用本地 Ollama 生成向量。

    **为什么提供本地后端**：
    - 完全离线、零成本，不消耗任何云端额度；
    - 数据不出内网，适合对数据合规有要求的企业部署场景；
    - 与对话模型共用一套 Ollama 服务，无需额外基础设施。

    前置条件：`ollama pull <向量模型>`，且服务端需启用 embeddings
    （部分版本报 "This server does not support embeddings" 时需重启服务）。
    """
    model = settings.ollama_embedding_model
    url = f"{settings.ollama_base_url}/api/embed"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json={"model": model, "input": texts})
        if resp.status_code == 404:
            raise RuntimeError(
                f"Ollama 未启用向量化接口。请先执行 `ollama pull {model}`，"
                "并确认服务端已支持 embeddings。"
            )
        resp.raise_for_status()
        data = resp.json()

    embeddings = data.get("embeddings")
    if not embeddings:
        raise RuntimeError(f"Ollama 未返回向量数据：{str(data)[:200]}")
    # Ollama 用 prompt_eval_count 表示输入 token 数，归一化成与云端一致的形状
    prompt_tokens = int(data.get("prompt_eval_count") or 0)
    await _record_embedding_usage(
        "ollama", model, len(texts),
        {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    )
    return embeddings


async def _record_embedding_usage(provider: str, model: str, count: int, usage) -> None:
    """记录一次向量化调用的用量。

    向量化的 token 消耗容易被忽略，但在知识库初始化/重建时它往往是成本大头 ——
    整库几十上百条文本会一次性全部送去向量化，因此必须单独统计。

    服务端未返回 usage 时不臆造 token 数（记 0），但这一次调用本身仍会被计入 ——
    「被调用了多少次」是可确证的事实，而「用了多少 token」在拿不到时就不该编。
    """
    from app.services import usage_service

    payload = usage or {}
    prompt_tokens = int(payload.get("prompt_tokens") or 0)
    await usage_service.record(
        scene=usage_service.SCENE_EMBEDDING,
        provider=provider,
        model=model,
        tier=(
            usage_service.TIER_LOCAL if provider == "ollama"
            else usage_service.TIER_CLOUD
        ),
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
    )


async def _embed_batch(texts: list) -> list:
    """按配置的向量化后端分发（auto 已在 settings 中解析成具体后端）。"""
    backend = settings.resolved_embedding_backend
    logger.debug("向量化后端：%s（%d 条文本）", backend, len(texts))
    if backend == "ollama":
        return await _embed_batch_ollama(texts)
    return await _embed_batch_zhipu(texts)


async def embed_texts(texts: list) -> list:
    """批量文本向量化，返回与输入顺序一致的向量列表。

    - 命中内存缓存的文本直接复用，不再请求 API；
    - 新文本按 _CHUNK_SIZE 分批调用 embedding API；
    - 未配置 key 或接口失败时抛异常，由调用方降级。
    """
    if not texts:
        return []
    backend = settings.resolved_embedding_backend
    if backend == "zhipu" and not settings.zhipuai_api_key:
        raise RuntimeError(
            "未配置 ZHIPUAI_API_KEY，无法使用云端向量化"
            "（可设 EMBEDDING_BACKEND=ollama 改用本地模型）"
        )

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
