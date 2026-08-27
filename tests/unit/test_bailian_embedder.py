"""验证百炼 Embedding 适配器的批处理边界。"""

from types import SimpleNamespace
from typing import cast

import pytest

from ultimate_rag.embeddings import BailianEmbedder


class FakeEmbeddingsAPI:
    """记录请求批次并返回确定性向量，避免单元测试访问真实百炼服务。"""

    def __init__(self) -> None:
        """初始化用于断言的批次大小记录。"""

        self.batch_sizes: list[int] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        """模拟 OpenAI 兼容 Embedding API，并保持输入与输出顺序一致。"""

        inputs = cast(list[str], kwargs["input"])
        self.batch_sizes.append(len(inputs))
        data = [
            SimpleNamespace(index=index, embedding=[float(index), 1.0])
            for index in range(len(inputs))
        ]
        return SimpleNamespace(data=data)


@pytest.mark.asyncio
async def test_embedder_batches_requests() -> None:
    """当输入超过单批上限时，应拆批且最终不丢失任何向量。"""

    embedder = BailianEmbedder(
        api_key="test", base_url="https://example.test/v1", model="test", dimension=2, batch_size=2
    )
    fake_api = FakeEmbeddingsAPI()
    embedder._client = SimpleNamespace(embeddings=fake_api)  # type: ignore[assignment]

    vectors = await embedder.embed_documents(["a", "b", "c"])

    assert fake_api.batch_sizes == [2, 1]
    assert len(vectors) == 3
