"""阿里云百炼文本 Embedding 适配器。

模块职责：
    通过百炼的 OpenAI-Compatible API 把文档文本和用户 Query 转换为稠密向量，并保证
    输入顺序、输出数量与向量维度满足领域层 ``Embedder`` 端口的契约。

架构边界：
    本模块只处理模型协议、Batch 和响应校验，不决定如何 Chunk、不写入 Milvus，也不编排
    文档状态。Application Service 只面向 Embedder 行为，不依赖 OpenAI SDK 响应模型。

设计背景：
    文档和 Query 必须使用同一模型与维度才能进入同一向量空间。供应商限制单批输入数量，
    因此分批逻辑集中在 Adapter 内部，避免调用方了解外部 API 限制或逐 Chunk 发请求。

外部约束：
    V1 默认使用 ``text-embedding-v4``、1024 维和最多 10 条文本一批。维度、Batch 与 Timeout
    均来自 Settings；API 调用会产生费用，空输入会在本地直接返回，不访问外部服务。

失败行为：
    网络、超时或协议异常原样上抛；数量或维度异常转换为 ``RuntimeError``，阻止错误向量
    进入 Milvus。V1 不在 Adapter 内自动重试，避免对非临时错误和计费请求进行隐式重复调用。
"""

from collections.abc import Sequence

from openai import AsyncOpenAI


class BailianEmbedder:
    """把可复用百炼异步客户端适配为领域层 ``Embedder`` 端口。

    实例固定模型、维度和 Batch 大小，供文档摄取与 Query Retrieval 共享。它不缓存向量，
    也不修改文本；输入清洗和 Chunk 策略属于上游职责。
    """

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
        """按供应商 Batch 上限向量化文本，并恢复与输入一致的向量顺序。

        Args:
            texts: 已完成切块的文本序列；返回向量必须与该顺序一一对应。

        Returns:
            与 ``texts`` 等长的二维浮点列表，每个向量维度等于配置的 ``dimension``。

        Raises:
            RuntimeError: 响应向量总数或任一向量维度不符合契约。
            Exception: OpenAI-Compatible 客户端产生的网络、超时或协议错误。

        Side Effects:
            非空输入会调用外部计费 API；V1 串行执行 Batch，不进行 Adapter 内重试。
        """

        if not texts:
            # 空序列的合法结果就是空序列。提前返回可避免一次无意义的计费网络请求，
            # 同时保持“每条输入恰好对应一条向量”的长度契约。
            return []
        embeddings: list[list[float]] = []

        # 阶段 1 — Request Batches：按配置上限切片，并串行调用外部 API。
        # 串行策略让 V1 的费用、失败位置和限流行为更可预测；有性能数据前不引入并发控制。
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimension,
                encoding_format="float",
            )

            # 阶段 2 — Restore Order：协议使用 ``index`` 关联向量与本批输入，服务端返回顺序
            # 不属于可靠契约。必须先排序再合并，否则 Chunk 可能绑定另一个文本的向量。
            ordered = sorted(response.data, key=lambda item: item.index)
            embeddings.extend([list(item.embedding) for item in ordered])

        # 阶段 3 — Validate Shape：所有 Batch 合并后统一检查总数与固定维度。
        # 形状异常必须在进入 Milvus 前失败；否则可能发生 Chunk/Vector 错配或 Schema 拒绝写入。
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
