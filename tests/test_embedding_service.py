"""embedding_service 的单元测试。

覆盖：
- cosine：余弦相似度计算（相同 / 正交 / 空向量 / 长度不一致 / 已知值）
- embed_texts：空输入 / 无 key 抛异常 / 命中缓存不重复请求 / 顺序与输入一致
- invalidate_embedding_cache：清空缓存

运行方式（项目根目录）：
    python -m pytest tests/test_embedding_service.py -v
"""
import asyncio
from unittest.mock import AsyncMock, patch

import app.services.embedding_service as es
from app.services.embedding_service import cosine, embed_texts, invalidate_embedding_cache


class TestCosine:
    def test_相同向量为1(self):
        assert abs(cosine([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-9

    def test_正交向量为0(self):
        assert abs(cosine([1, 0], [0, 1])) < 1e-9

    def test_已知值(self):
        # [1,1] 与 [1,0] 的夹角余弦 = 1/√2
        assert abs(cosine([1, 1], [1, 0]) - (1 / (2 ** 0.5))) < 1e-9

    def test_空向量为0(self):
        assert cosine([], []) == 0.0

    def test_长度不一致为0(self):
        assert cosine([1, 2], [1, 2, 3]) == 0.0


class TestEmbedTexts:
    def test_空输入返回空(self):
        assert asyncio.run(embed_texts([])) == []

    def test_无key抛异常(self):
        with patch.object(es.settings, "zhipuai_api_key", ""):
            try:
                asyncio.run(embed_texts(["你好"]))
                assert False, "未配置 key 时应抛出 RuntimeError"
            except RuntimeError:
                pass

    def test_命中缓存不重复请求(self):
        invalidate_embedding_cache()
        with patch.object(es.settings, "zhipuai_api_key", "fake-key"), \
             patch.object(es, "_embed_batch", new=AsyncMock(return_value=[[0.1, 0.2]])) as mock_batch:
            first = asyncio.run(embed_texts(["同一句话"]))
            second = asyncio.run(embed_texts(["同一句话"]))
            # 第二次命中缓存，不再请求 API
            assert mock_batch.await_count == 1
            assert first == second == [[0.1, 0.2]]

    def test_顺序与输入一致(self):
        invalidate_embedding_cache()
        with patch.object(es.settings, "zhipuai_api_key", "fake-key"), \
             patch.object(es, "_embed_batch", new=AsyncMock(return_value=[[1.0], [2.0]])) as mock_batch:
            out = asyncio.run(embed_texts(["甲", "乙"]))
            assert out == [[1.0], [2.0]]
            mock_batch.assert_awaited_once_with(["甲", "乙"])


class TestInvalidate:
    def test_清空缓存后需重新请求(self):
        invalidate_embedding_cache()
        with patch.object(es.settings, "zhipuai_api_key", "fake-key"), \
             patch.object(es, "_embed_batch", new=AsyncMock(return_value=[[9.9]])) as mock_batch:
            asyncio.run(embed_texts(["缓存失效测试"]))
            invalidate_embedding_cache()
            asyncio.run(embed_texts(["缓存失效测试"]))
            assert mock_batch.await_count == 2
