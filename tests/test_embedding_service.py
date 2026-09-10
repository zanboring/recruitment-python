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

import pytest

import app.services.embedding_service as es
from app.services.embedding_service import cosine, embed_texts, invalidate_embedding_cache


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch):
    """本文件内禁止真实网络请求。

    ``embedding_service`` 在后端解析为 ollama 时会真的发起 HTTP 请求。这条路径
    曾让 ``test_无key抛异常`` **偶发**挂掉：失败信息是 httpx 的 ConnectError，
    完全不指向真正的原因（patch 没配对），而且要连跑多次才复现一次 ——
    这种「随机失败 + 错误归因」的组合最消耗排查时间。

    这里把网络出口物理封死：任何走真网络的路径都会立刻以一句明确的断言信息
    失败，而不是随机挂在某个用例上。本文件其余用例都已 mock 掉 ``_embed_batch``，
    因此不会受影响。
    """

    class _BlockedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "embedding 测试不应发起真实网络请求：请确认 embedding_backend 与 "
                "zhipuai_api_key 的 patch 成对设置，并检查被测代码是否回退到了 ollama 后端"
            )

    monkeypatch.setattr(es.httpx, "AsyncClient", _BlockedClient)


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
        # 锁定 backend=zhipu：auto 模式下空 key 会回退到 ollama，验证「zhipu 无 key」
        # 抛 RuntimeError 这一行为就失去了意义。
        #
        # 两个 patch 必须一起设。这里额外**断言 patch 已生效** —— 一旦失效，
        # 用例会以网络异常的形式偶发失败，错误信息指不到真正的原因；
        # 有了这行断言，失效时会直接指出问题所在（网络出口另见 _forbid_real_network）。
        with patch.object(es.settings, "zhipuai_api_key", ""), \
             patch.object(es.settings, "embedding_backend", "zhipu"):
            assert es.settings.resolved_embedding_backend == "zhipu"

            with pytest.raises(RuntimeError, match="ZHIPUAI_API_KEY"):
                asyncio.run(embed_texts(["你好"]))

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
