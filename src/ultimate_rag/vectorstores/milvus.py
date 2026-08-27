"""Milvus 稠密向量索引适配器。

Milvus 是可由 PostgreSQL Chunk 与 MinIO 原文件重建的派生索引。PyMilvus 客户端为同步接口，
网络操作统一放入工作线程，避免阻塞 API 事件循环。
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from pymilvus import DataType, MilvusClient  # type: ignore[import-untyped]

from ultimate_rag.domain.models import EmbeddedChunk, RetrievalResult


class MilvusVectorStore:
    """实现 V1 固定 Schema、余弦检索和按业务范围删除的向量存储。"""

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
        """使用 PyMilvus 同步 API 创建 V1 静态 Schema 和余弦索引。"""
        if self._client.has_collection(self._collection):
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
        schema.add_field("filename", DataType.VARCHAR, max_length=512)
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("heading_path", DataType.JSON)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self._dimension)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    async def upsert(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """按稳定 Chunk ID 幂等写入向量及 Citation 所需元数据。"""
        if not chunks:
            return
        rows = [
            {
                "id": item.chunk.id,
                "knowledge_base_id": item.chunk.knowledge_base_id,
                "document_id": item.chunk.document_id,
                "chunk_id": item.chunk.id,
                "filename": str(item.chunk.metadata.get("filename", "")),
                "content": item.chunk.content,
                "heading_path": list(item.chunk.heading_path),
                "embedding": list(item.embedding),
            }
            for item in chunks
        ]
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
        """在知识库范围内执行 Dense Search，并映射为领域检索结果。"""
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
        """把 PyMilvus Hit 结构映射为不依赖 SDK 的领域结果。"""
        entity = hit["entity"]
        return RetrievalResult(
            chunk_id=str(entity["chunk_id"]),
            knowledge_base_id=str(entity["knowledge_base_id"]),
            document_id=str(entity["document_id"]),
            filename=str(entity["filename"]),
            content=str(entity["content"]),
            heading_path=tuple(entity.get("heading_path") or []),
            score=float(hit["distance"]),
        )
