"""验证跨 PostgreSQL、MinIO 和 Milvus 的同步删除编排。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import DocumentLifecycleService
from ultimate_rag.domain.models import DocumentStatus
from ultimate_rag.domain.ports import ObjectStorage, VectorStore
from ultimate_rag.infrastructure.database.repository import Repository


@pytest.mark.asyncio
async def test_delete_knowledge_base_keeps_fact_delete_last() -> None:
    """所有派生向量和原文件清理后，才能删除 PostgreSQL 事实记录。"""

    events: list[str] = []
    repository = AsyncMock()
    repository.list_documents.return_value = [
        SimpleNamespace(object_key="kb-1/doc-1/source.md", status=DocumentStatus.READY),
        SimpleNamespace(object_key="kb-1/doc-2/source.md", status=DocumentStatus.READY),
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
    service = DocumentLifecycleService(
        cast(Repository, repository),
        cast(ObjectStorage, storage),
        cast(VectorStore, vector_store),
    )

    await service.delete_knowledge_base("kb-1")

    assert events == [
        "delete-vectors",
        "delete-object:kb-1/doc-1/source.md",
        "delete-object:kb-1/doc-2/source.md",
        "delete-facts",
    ]


@pytest.mark.asyncio
async def test_delete_knowledge_base_preserves_facts_when_object_cleanup_fails() -> None:
    """MinIO 清理中断时必须保留数据库事实，供后续定位和补偿。"""

    repository = AsyncMock()
    repository.list_documents.return_value = [
        SimpleNamespace(object_key="kb-1/doc-1/source.md", status=DocumentStatus.READY)
    ]
    storage = AsyncMock()
    storage.delete.side_effect = RuntimeError("MinIO unavailable")
    vector_store = AsyncMock()
    service = DocumentLifecycleService(
        cast(Repository, repository),
        cast(ObjectStorage, storage),
        cast(VectorStore, vector_store),
    )

    with pytest.raises(RuntimeError, match="MinIO unavailable"):
        await service.delete_knowledge_base("kb-1")

    repository.delete_knowledge_base.assert_not_awaited()
