"""验证跨 PostgreSQL、MinIO、Milvus 和本地快照的同步删除编排。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import DocumentLifecycleService
from ultimate_rag.domain.models import DocumentStatus
from ultimate_rag.domain.ports import ChunkSnapshotStore, ObjectStorage, VectorStore
from ultimate_rag.infrastructure.database.repository import Repository


@pytest.mark.asyncio
async def test_delete_knowledge_base_keeps_fact_delete_last() -> None:
    """派生向量、本地快照和原文件清理后，才能删除 PostgreSQL 事实记录。"""

    events: list[str] = []
    repository = AsyncMock()
    repository.list_documents.return_value = [
        SimpleNamespace(id="doc-1", object_key="kb-1/doc-1/source.md", status=DocumentStatus.READY),
        SimpleNamespace(id="doc-2", object_key="kb-1/doc-2/source.md", status=DocumentStatus.READY),
    ]
    repository.list_document_assets.return_value = [
        SimpleNamespace(object_key="kb-1/doc-1/assets/figure.jpg")
    ]
    repository.delete_knowledge_base.side_effect = lambda _knowledge_base_id: events.append(
        "delete-facts"
    )
    storage = AsyncMock()
    storage.delete.side_effect = lambda object_key: events.append(f"delete-object:{object_key}")
    vector_store = AsyncMock()
    vector_store.delete_by_knowledge_base.side_effect = lambda _knowledge_base_id: events.append(
        "delete-vectors"
    )
    chunk_snapshot_store = AsyncMock()
    chunk_snapshot_store.delete_by_knowledge_base.side_effect = lambda _knowledge_base_id: (
        events.append("delete-chunk-snapshots")
    )
    service = DocumentLifecycleService(
        cast(Repository, repository),
        cast(ObjectStorage, storage),
        cast(VectorStore, vector_store),
        cast(ChunkSnapshotStore, chunk_snapshot_store),
    )

    await service.delete_knowledge_base("kb-1")

    assert events == [
        "delete-vectors",
        "delete-chunk-snapshots",
        "delete-object:kb-1/doc-1/assets/figure.jpg",
        "delete-object:kb-1/doc-1/source.md",
        "delete-object:kb-1/doc-2/source.md",
        "delete-facts",
    ]


@pytest.mark.asyncio
async def test_delete_knowledge_base_preserves_facts_when_object_cleanup_fails() -> None:
    """MinIO 清理中断时必须保留数据库事实，供后续定位和补偿。"""

    repository = AsyncMock()
    repository.list_documents.return_value = [
        SimpleNamespace(id="doc-1", object_key="kb-1/doc-1/source.md", status=DocumentStatus.READY)
    ]
    repository.list_document_assets.return_value = []
    storage = AsyncMock()
    storage.delete.side_effect = RuntimeError("MinIO unavailable")
    vector_store = AsyncMock()
    chunk_snapshot_store = AsyncMock()
    service = DocumentLifecycleService(
        cast(Repository, repository),
        cast(ObjectStorage, storage),
        cast(VectorStore, vector_store),
        cast(ChunkSnapshotStore, chunk_snapshot_store),
    )

    with pytest.raises(RuntimeError, match="MinIO unavailable"):
        await service.delete_knowledge_base("kb-1")

    repository.delete_knowledge_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_knowledge_base_preserves_facts_when_snapshot_cleanup_fails() -> None:
    """本地明文清理失败时不能删除事实或谎报知识库已经完整删除。"""

    repository = AsyncMock()
    repository.list_documents.return_value = [
        SimpleNamespace(id="doc-1", object_key="kb-1/doc-1/source.md", status=DocumentStatus.READY)
    ]
    repository.list_document_assets.return_value = []
    storage = AsyncMock()
    vector_store = AsyncMock()
    chunk_snapshot_store = AsyncMock()
    chunk_snapshot_store.delete_by_knowledge_base.side_effect = OSError("snapshot disk busy")
    service = DocumentLifecycleService(
        cast(Repository, repository),
        cast(ObjectStorage, storage),
        cast(VectorStore, vector_store),
        cast(ChunkSnapshotStore, chunk_snapshot_store),
    )

    with pytest.raises(OSError, match="snapshot disk busy"):
        await service.delete_knowledge_base("kb-1")

    storage.delete.assert_not_awaited()
    repository.delete_knowledge_base.assert_not_awaited()
