"""阿里云百炼文本向量适配器。

通过 OpenAI 兼容协议调用模型。V1 默认 ``text-embedding-v4``，其批量输入最多 10 条，
因此适配器负责有界分批、恢复响应顺序并校验向量数量与维度。
"""

from collections.abc import Sequence

from openai import AsyncOpenAI


class BailianEmbedder:
    """把百炼 Embeddings API 适配为领域层 ``Embedder`` 端口。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        batch_size: int = 10,
        timeout: float = 60.0,
    ) -> None:
        """创建可复用异步客户端，并记录模型维度和官方批量限制。"""
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """按配置批次向量化文本，并严格校验模型响应的形状。"""
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimension,
                encoding_format="float",
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            embeddings.extend([list(item.embedding) for item in ordered])
        self._validate(embeddings, len(texts))
        return embeddings

    async def embed_query(self, query: str) -> list[float]:
        """在同一模型和维度中向量化单个查询。"""
        result = await self.embed_documents([query])
        return result[0]

    def _validate(self, embeddings: list[list[float]], expected_count: int) -> None:
        """拒绝数量或维度异常的响应，避免污染 Milvus Collection。"""
        if len(embeddings) != expected_count:
            raise RuntimeError(
                f"Embedding service returned {len(embeddings)} vectors; expected {expected_count}"
            )
        if any(len(vector) != self._dimension for vector in embeddings):
            raise RuntimeError("Embedding service returned an unexpected vector dimension")
