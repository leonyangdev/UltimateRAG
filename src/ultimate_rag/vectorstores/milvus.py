"""Milvus 稠密向量索引适配器。

模块职责：
    把 ``EmbeddedChunk`` 写入固定 Schema 的 Milvus Collection，执行知识库范围内的
    COSINE Dense Retrieval，并把 SDK Hit 映射回 ``RetrievalResult`` 领域对象。

架构边界：
    Milvus 只保存可由 PostgreSQL Chunk 与 MinIO 原文件重建的派生索引，不是文档状态或
    业务事实来源。本模块不决定摄取状态、Embedding 模型、Prompt 或删除工作流顺序。

设计背景：
    V1 使用稳定 Chunk ID 作为主键支持幂等 Upsert，使用 Strong Consistency 保证文档变为
    READY 后可立即检索、删除成功后立即不可见。未有性能数据前使用 AUTOINDEX，避免过早调参。

外部约束：
    PyMilvus 客户端是同步接口，所有网络调用通过 ``asyncio.to_thread`` 移出 Event Loop。
    Collection 向量维度必须与 Embedder 完全一致；写入和删除后显式 Flush 再向上层返回。

注意事项 / 已知限制：
    删除后的物理 Row Count 可能暂时包含等待 Compaction 的 Tombstone，业务可见性应通过
    Strong Query/Search 判断。V1 只实现 Dense Retrieval，不包含 Sparse Search 或 Rerank。
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from pymilvus import DataType, MilvusClient  # type: ignore[import-untyped]

from ultimate_rag.domain.models import EmbeddedChunk, RetrievalResult, SourceLocator


class MilvusVectorStore:
    """实现领域 ``VectorStore`` 端口的 V1 Milvus Adapter。

    实例复用一个同步 ``MilvusClient``，固定 Collection 与向量维度。它负责 SDK 数据映射、
    Flush 和线程切换，不负责数据库事实、Chunk 生成或文档状态推进。
    """

    def __init__(
        self,
        *,
        uri: str,
        collection: str,
        dimension: int,
        token: str | None = None,
    ) -> None:
        """创建共享 Milvus 客户端，并固定 Collection 名称与向量维度。"""
        self._client = MilvusClient(uri=uri, token=token)
        self._collection = collection
        self._dimension = dimension

    async def ensure_collection(self) -> None:
        """幂等创建 Collection、字段 Schema 与 AUTOINDEX 余弦索引。"""
        await asyncio.to_thread(self._ensure_collection_sync)

    def _ensure_collection_sync(self) -> None:
        """使用同步 PyMilvus API 幂等创建 V1 Schema 与 COSINE 索引。

        已存在的 Collection 会直接复用。本方法不会在线修改已有 Schema；字段或维度变化必须
        通过明确的索引迁移或重建流程完成，不能在应用启动时隐式修改生产数据结构。
        """

        # 阶段 1 — Existence Check：Lifespan 在每次进程启动时调用本方法。
        # 已存在即返回，使启动幂等；这也意味着配置与现有 Schema 不兼容时不会自动迁移。
        if self._client.has_collection(self._collection):
            return

        # 阶段 2 — Define Schema：关闭 Dynamic Field，让字段拼写和类型错误尽早失败。
        # 主键使用应用生成的稳定 Chunk ID，而不是 Milvus Auto ID，摄取重试才能覆盖同一实体。
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
        schema.add_field("filename", DataType.VARCHAR, max_length=512)
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("heading_path", DataType.JSON)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self._dimension)

        # 阶段 3 — Define Index：COSINE 比较查询与文档向量的方向相似度，是当前 Embedding
        # 检索配置的一部分；AUTOINDEX 把物理索引选择交给 Milvus，V1 暂不做无数据调优。
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

        # 阶段 4 — Create Collection：Strong Consistency 让刚完成的 Upsert/Delete 立即可见。
        # V1 优先保证 READY 与检索、204 与删除之间的确定关系，接受相应的一致性性能成本。
        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    async def upsert(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """按稳定 Chunk ID 幂等写入向量和检索展示所需的最小元数据。

        空输入直接返回；非空输入会在线程中调用同步 SDK，并在 Upsert 后 Flush。只有 Flush
        成功，本方法才返回，Application Service 才能继续把文档标记为 READY。
        """

        if not chunks:
            return

        # 阶段 1 — Map Rows：把不可变领域对象转换为 SDK 接受的可序列化字典。
        # Citation 字段作为派生副本随向量保存，Search 后无需逐 Hit 查询 PostgreSQL；
        # PostgreSQL 仍是事实来源，Collection 丢失后可以从事实数据完整重建。
        rows = [
            {
                "id": item.chunk.id,
                "knowledge_base_id": item.chunk.knowledge_base_id,
                "document_id": item.chunk.document_id,
                "chunk_id": item.chunk.id,
                "filename": str(item.chunk.metadata.get("filename", "")),
                "content": item.chunk.content,
                # 复用 V1 已有 JSON 字段保存 V2 SourceLocator，避免在应用启动时隐式迁移
                # Milvus Schema。读取端兼容旧版 list，现有 V1 索引可以原地继续检索。
                "heading_path": (
                    item.chunk.locator.to_metadata()
                    if item.chunk.locator
                    else {"heading_path": list(item.chunk.heading_path)}
                ),
                "embedding": list(item.embedding),
            }
            for item in chunks
        ]
        # 阶段 2 — Persist：同步 Upsert/Flush 移入工作线程，避免阻塞 FastAPI Event Loop。
        await asyncio.to_thread(self._upsert_sync, rows)

    def _upsert_sync(self, rows: list[dict[str, Any]]) -> None:
        """写入并落盘向量，确保调用方只有在持久化完成后才能设置 ``READY``。"""
        self._client.upsert(collection_name=self._collection, data=rows)
        self._client.flush(collection_name=self._collection)

    async def search(
        self,
        query_vector: Sequence[float],
        knowledge_base_id: str,
        top_k: int,
    ) -> list[RetrievalResult]:
        """在单个知识库范围内执行 COSINE Dense Search。

        Args:
            query_vector: 与 Collection 使用相同 Embedding 模型和维度生成的查询向量。
            knowledge_base_id: 必须应用到 Milvus Filter 的业务隔离范围。
            top_k: 最多返回的候选数量，由 API Schema 限定合法区间。

        Returns:
            按 Milvus 相似度顺序排列的领域检索结果。
        """

        # 阶段 1 — Search：Milvus ``data`` 支持多个 Query，因此即使单查询也需要二维列表。
        # knowledge_base_id Filter 是检索隔离边界，不能先全库召回再由应用层过滤。
        result = await asyncio.to_thread(
            self._client.search,
            collection_name=self._collection,
            data=[list(query_vector)],
            filter=f'knowledge_base_id == "{knowledge_base_id}"',
            limit=top_k,
            output_fields=[
                "knowledge_base_id",
                "document_id",
                "chunk_id",
                "filename",
                "content",
                "heading_path",
            ],
        )

        # 阶段 2 — Map Hits：响应外层按 Query 分组，本次只读取第一组；无命中返回空列表。
        # 每个 SDK Hit 随即转换为领域对象，避免 entity/distance 字典结构泄漏到 Application。
        hits: list[dict[str, Any]] = result[0] if result else []
        return [self._retrieval_result(hit) for hit in hits]

    async def delete_by_document(self, document_id: str) -> None:
        """删除文档的派生向量，使重建与删除操作保持幂等。"""
        await asyncio.to_thread(self._delete_sync, f'document_id == "{document_id}"')

    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None:
        """删除整个知识库范围内的派生向量。"""
        await asyncio.to_thread(
            self._delete_sync,
            f'knowledge_base_id == "{knowledge_base_id}"',
        )

    def _delete_sync(self, filter_expression: str) -> None:
        """提交并落盘删除，使 204 响应表示向量清理已经持久化。"""
        self._client.delete(
            collection_name=self._collection,
            filter=filter_expression,
        )
        self._client.flush(collection_name=self._collection)

    @staticmethod
    def _retrieval_result(hit: dict[str, Any]) -> RetrievalResult:
        """把 PyMilvus Hit 映射为不依赖 SDK 的不可变领域结果。"""

        # ``output_fields`` 位于 entity，COSINE 分数位于 Hit 顶层 distance。
        # heading_path 兼容缺失字段或 JSON null，并在领域边界冻结为 Tuple。
        entity = hit["entity"]
        raw_locator = entity.get("heading_path")
        if isinstance(raw_locator, dict):
            locator = SourceLocator.from_metadata(raw_locator)
        else:
            # V1 行只保存标题数组；转换为统一 Locator 后无需重建已有 Collection。
            locator = SourceLocator(heading_path=tuple(str(item) for item in (raw_locator or [])))
        return RetrievalResult(
            chunk_id=str(entity["chunk_id"]),
            knowledge_base_id=str(entity["knowledge_base_id"]),
            document_id=str(entity["document_id"]),
            filename=str(entity["filename"]),
            content=str(entity["content"]),
            heading_path=locator.heading_path,
            score=float(hit["distance"]),
            locator=locator,
        )
