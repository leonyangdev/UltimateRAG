"""Milvus Dense + BM25 双索引适配器。

模块职责：
    保留 V2 COSINE Dense Collection，并把同一 Chunk 写入 V3 BM25 Sparse Collection；两种
    搜索都映射为统一 ``RetrievalResult``，供应用层做 RRF 和 Rerank。

架构边界：
    Milvus 只保存可由 PostgreSQL Chunk 与 MinIO 原文件重建的派生索引，不是文档状态或
    业务事实来源。本模块不决定摄取状态、Embedding 模型、Prompt 或删除工作流顺序。

设计背景：
    Milvus Collection 不能在线增加 BM25 Function，因此 V3 采用独立 Sparse Collection 旁路
    升级。历史 Dense 数据继续可用，Sparse 数据可直接由 PostgreSQL Chunk 回填，无需再次调用
    计费 Embedding。BM25 使用 Milvus 本地 Function，不依赖线上分词或稀疏模型服务。

外部约束：
    PyMilvus 客户端是同步接口，所有网络调用通过 ``asyncio.to_thread`` 移出 Event Loop。
    Collection 向量维度必须与 Embedder 完全一致；写入和删除后显式 Flush 再向上层返回。

注意事项 / 已知限制：
    中文技术文档使用 ``jieba + lowercase``，保留中英文型号；BM25 的 ``k1=1.2``、``b=0.75``
    是 Milvus 默认基线，不代表脱离真实评估集的全局最优值。RRF 与 Rerank 不属于本适配器。
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from pymilvus import (  # type: ignore[import-untyped]
    DataType,
    Function,
    FunctionType,
    MilvusClient,
)

from ultimate_rag.domain.models import Chunk, EmbeddedChunk, RetrievalResult, SourceLocator


class MilvusVectorStore:
    """实现领域 ``VectorStore`` 端口的 V3 Milvus Adapter。

    实例复用一个同步 ``MilvusClient``，固定 Collection 与向量维度。它负责 SDK 数据映射、
    Flush 和线程切换，不负责数据库事实、Chunk 生成或文档状态推进。
    """

    def __init__(
        self,
        *,
        uri: str,
        collection: str,
        dimension: int,
        sparse_collection: str = "knowledge_chunks_sparse_v3",
        token: str | None = None,
        bm25_k1: float = 1.2,
        bm25_b: float = 0.75,
    ) -> None:
        """创建共享客户端并固定两个派生索引的 Schema 参数。"""
        self._client = MilvusClient(uri=uri, token=token)
        self._collection = collection
        self._sparse_collection = sparse_collection
        self._dimension = dimension
        self._bm25_k1 = bm25_k1
        self._bm25_b = bm25_b

    async def ensure_collection(self) -> None:
        """幂等准备 V2 Dense 与 V3 BM25 两个 Collection。"""
        await asyncio.to_thread(self._ensure_dense_collection_sync)
        await asyncio.to_thread(self._ensure_sparse_collection_sync)

    def _ensure_dense_collection_sync(self) -> None:
        """使用同步 PyMilvus API 幂等创建 V2 Schema 与 COSINE 索引。

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

    def _ensure_sparse_collection_sync(self) -> None:
        """幂等创建带本地中文分析器和 BM25 Function 的 V3 Sparse Collection。

        ``content`` 是 Function 输入，Milvus 在写入时生成 ``sparse_embedding``。应用不保存
        供应商私有稀疏向量，因此分析器或 BM25 参数变化时可以从 PostgreSQL 原文完整重建。
        """

        if self._client.has_collection(self._sparse_collection):
            return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
        schema.add_field("filename", DataType.VARCHAR, max_length=512)
        schema.add_field(
            "content",
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            # Milvus 的 standard analyzer 会把连续中文当作一个 Token。jieba 负责中文词边界，
            # lowercase 统一英文大小写且不会删除 CJK 或产品型号中的字母数字。
            analyzer_params={"tokenizer": "jieba", "filter": ["lowercase"]},
        )
        schema.add_field("heading_path", DataType.JSON)
        schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="content_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_embedding"],
            )
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="sparse_embedding",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={
                # DAAT_MAXSCORE 是 Milvus 的默认高效 BM25 算法；在当前候选宽度下优先保持
                # 精确结果，不设置 drop_ratio_search 近似丢词。
                "inverted_index_algo": "DAAT_MAXSCORE",
                "bm25_k1": self._bm25_k1,
                "bm25_b": self._bm25_b,
            },
        )
        self._client.create_collection(
            collection_name=self._sparse_collection,
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
        dense_rows = [
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
        # 阶段 2 — Persist：Dense 先写，Sparse 后写；任一步失败都会阻止 Document 进入 READY。
        # Worker 的失败补偿会同时删除两个 Collection 中该文档的行，因此部分写入可安全重试。
        await asyncio.to_thread(self._upsert_sync, dense_rows)
        await self.upsert_sparse([item.chunk for item in chunks])

    async def upsert_sparse(self, chunks: Sequence[Chunk]) -> None:
        """只写入 BM25 原始文本与来源字段，供历史索引无模型成本回填。"""

        if not chunks:
            return
        rows = [self._sparse_row(chunk) for chunk in chunks]
        await asyncio.to_thread(self._upsert_sparse_sync, rows)

    def _upsert_sync(self, rows: list[dict[str, Any]]) -> None:
        """写入并落盘向量，确保调用方只有在持久化完成后才能设置 ``READY``。"""
        self._client.upsert(collection_name=self._collection, data=rows)
        self._client.flush(collection_name=self._collection)

    def _upsert_sparse_sync(self, rows: list[dict[str, Any]]) -> None:
        """写入原文并等待 Milvus BM25 Function 与稀疏索引可见。"""

        self._client.upsert(collection_name=self._sparse_collection, data=rows)
        self._client.flush(collection_name=self._sparse_collection)

    async def search(
        self,
        query_vector: Sequence[float],
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
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
            filter=self._filter_expression(knowledge_base_id, document_ids),
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
        return [self._retrieval_result(hit, source="dense") for hit in hits]

    async def search_sparse(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
    ) -> list[RetrievalResult]:
        """使用 Milvus 内置 Analyzer 与 BM25 Function 执行本地全文检索。

        ``data`` 直接传原始查询字符串，Milvus 使用 Collection 创建时相同的 Analyzer 生成
        查询稀疏向量。BM25 分数越大排名越高，但只在同一请求内有比较意义。
        """

        result = await asyncio.to_thread(
            self._client.search,
            collection_name=self._sparse_collection,
            data=[query],
            anns_field="sparse_embedding",
            search_params={"metric_type": "BM25"},
            filter=self._filter_expression(knowledge_base_id, document_ids),
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
        hits: list[dict[str, Any]] = result[0] if result else []
        return [self._retrieval_result(hit, source="sparse") for hit in hits]

    async def delete_by_document(self, document_id: str) -> None:
        """删除文档的 Dense 与 Sparse 派生索引，使重建保持幂等。"""

        expression = f"document_id == {json.dumps(document_id)}"
        await asyncio.to_thread(self._delete_both_sync, expression)

    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None:
        """删除整个知识库范围内的派生向量。"""
        expression = f"knowledge_base_id == {json.dumps(knowledge_base_id)}"
        await asyncio.to_thread(self._delete_both_sync, expression)

    async def delete_sparse_by_knowledge_base(self, knowledge_base_id: str) -> None:
        """只清理一个知识库的 BM25 派生行，供显式回填工具执行精确重建。"""

        expression = f"knowledge_base_id == {json.dumps(knowledge_base_id)}"
        await asyncio.to_thread(self._delete_sparse_sync, expression)

    def _delete_both_sync(self, filter_expression: str) -> None:
        """顺序删除两个派生集合；任一步失败都向应用层报告，不能返回虚假成功。"""

        self._delete_sync(filter_expression)
        self._client.delete(
            collection_name=self._sparse_collection,
            filter=filter_expression,
        )
        self._client.flush(collection_name=self._sparse_collection)

    def _delete_sparse_sync(self, filter_expression: str) -> None:
        """提交并落盘单个 Sparse Collection 删除。"""

        self._client.delete(
            collection_name=self._sparse_collection,
            filter=filter_expression,
        )
        self._client.flush(collection_name=self._sparse_collection)

    def _delete_sync(self, filter_expression: str) -> None:
        """提交并落盘删除，使 204 响应表示向量清理已经持久化。"""
        self._client.delete(
            collection_name=self._collection,
            filter=filter_expression,
        )
        self._client.flush(collection_name=self._collection)

    @staticmethod
    def _retrieval_result(
        hit: dict[str, Any],
        *,
        source: str = "dense",
    ) -> RetrievalResult:
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
        score = float(hit["distance"])
        return RetrievalResult(
            chunk_id=str(entity["chunk_id"]),
            knowledge_base_id=str(entity["knowledge_base_id"]),
            document_id=str(entity["document_id"]),
            filename=str(entity["filename"]),
            content=str(entity["content"]),
            heading_path=locator.heading_path,
            score=score,
            locator=locator,
            dense_score=score if source == "dense" else None,
            sparse_score=score if source == "sparse" else None,
            retrieval_sources=(source,),
            context_chunk_ids=(str(entity["chunk_id"]),),
        )

    @staticmethod
    def _sparse_row(chunk: Chunk) -> dict[str, Any]:
        """把 PostgreSQL 可重建 Chunk 映射为 BM25 Collection 行。"""

        return {
            "id": chunk.id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "document_id": chunk.document_id,
            "chunk_id": chunk.id,
            "filename": str(chunk.metadata.get("filename", "")),
            "content": chunk.content,
            "heading_path": (
                chunk.locator.to_metadata()
                if chunk.locator
                else {"heading_path": list(chunk.heading_path)}
            ),
        }

    @staticmethod
    def _filter_expression(
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> str:
        """用 JSON 字符串转义构造只含受控字段的 Milvus 标量过滤表达式。

        字段名和操作符固定在代码中；值使用 JSON 编码而不是字符串插值，避免引号或反斜杠
        破坏表达式。API 最多允许 50 个文档 ID，因此 ``in`` 列表不会无界增长。
        """

        expression = f"knowledge_base_id == {json.dumps(knowledge_base_id)}"
        if document_ids:
            encoded_ids = ", ".join(json.dumps(value) for value in document_ids)
            expression += f" and document_id in [{encoded_ids}]"
        return expression
