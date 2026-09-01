"""验证文档处理管线中 Chunk 快照的边界、顺序和失败语义。"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from ultimate_rag.application import DocumentProcessingService
from ultimate_rag.domain.models import (
    Chunk,
    Document,
    DocumentStatus,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.domain.ports import (
    Chunker,
    ChunkSnapshotStore,
    Embedder,
    ObjectStorage,
    VectorStore,
)
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.parsers.registry import ParserRegistry


def _document(status: DocumentStatus = DocumentStatus.PENDING) -> Document:
    """构造后台 Worker 已领取但尚未完成的文档事实。"""

    now = datetime.now(UTC)
    return Document(
        id="doc-1",
        knowledge_base_id="kb-1",
        filename="paper.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        object_key="kb-1/doc-1/source.pdf",
        sha256="source-sha",
        status=status,
        parser_name=None,
        parser_version=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _service() -> tuple[
    DocumentProcessingService,
    AsyncMock,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    """装配一条不访问真实数据库、模型或 Milvus 的完整处理管线。"""

    document = _document()
    repository = AsyncMock()
    repository.get_document.side_effect = [
        document,
        replace(document, status=DocumentStatus.READY),
    ]
    repository.list_document_assets.return_value = []

    parsed = ParsedDocument(
        document_id=document.id,
        blocks=(),
        metadata={"parser": "test-parser", "layout_engine": "fixture"},
    )
    parser = SimpleNamespace(
        name="test-parser",
        version="1.2",
        parse=AsyncMock(return_value=parsed),
    )
    parser_registry = MagicMock()
    parser_registry.resolve.return_value = parser

    locator = SourceLocator(heading_path=("Architecture",), page=2)
    chunker = AsyncMock()
    chunker.split.return_value = [
        Chunk(
            id="chunk-1",
            knowledge_base_id="kb-1",
            document_id="doc-1",
            index=0,
            content="Transformer architecture",
            heading_path=locator.heading_path,
            token_count=12,
            locator=locator,
            metadata={"split_strategy": "single", "parent_id": "parent-1"},
        )
    ]
    storage = AsyncMock()
    storage.get.return_value = b"pdf-bytes"
    chunk_snapshot_store = AsyncMock()
    embedder = AsyncMock()
    embedder.embed_documents.return_value = [[0.1, 0.2]]
    vector_store = AsyncMock()

    service = DocumentProcessingService(
        repository=cast(Repository, repository),
        storage=cast(ObjectStorage, storage),
        parser_registry=cast(ParserRegistry, parser_registry),
        chunker=cast(Chunker, chunker),
        chunk_snapshot_store=cast(ChunkSnapshotStore, chunk_snapshot_store),
        embedder=cast(Embedder, embedder),
        vector_store=cast(VectorStore, vector_store),
    )
    return service, repository, chunk_snapshot_store, embedder, vector_store


@pytest.mark.asyncio
async def test_process_snapshots_enriched_chunks_before_embedding_and_indexing() -> None:
    """快照必须接收最终 metadata，并严格先于付费 Embedding 和两类索引写入。"""

    service, repository, snapshot_store, embedder, vector_store = _service()
    events: list[str] = []

    snapshot_store.save.side_effect = lambda **_kwargs: events.append("snapshot")
    embedder.embed_documents.side_effect = lambda _texts: (
        events.append("embedding"),
        [[0.1, 0.2]],
    )[1]
    repository.replace_chunks.side_effect = lambda _document_id, _chunks: events.append(
        "postgres-chunks"
    )
    vector_store.delete_by_document.side_effect = lambda _document_id: events.append(
        "delete-vectors"
    )
    vector_store.upsert.side_effect = lambda _chunks: events.append("upsert-vectors")

    await service.process("doc-1")

    assert events == [
        "snapshot",
        "embedding",
        "postgres-chunks",
        "delete-vectors",
        "upsert-vectors",
    ]
    saved_chunks = snapshot_store.save.await_args.kwargs["chunks"]
    assert saved_chunks[0].metadata["filename"] == "paper.pdf"
    assert saved_chunks[0].metadata["source_locator"] == {
        "heading_path": ["Architecture"],
        "page": 2,
    }
    assert snapshot_store.save.await_args.kwargs["parsed_document"].metadata == {
        "parser": "test-parser",
        "layout_engine": "fixture",
    }
    repository.update_document_status.assert_any_await("doc-1", DocumentStatus.READY)


@pytest.mark.asyncio
async def test_snapshot_failure_stops_before_embedding_and_vector_mutation() -> None:
    """本地快照失败时不能继续产生模型费用、PostgreSQL Chunk 或半成品向量。"""

    service, repository, snapshot_store, embedder, vector_store = _service()
    snapshot_store.save.side_effect = OSError("snapshot disk full")

    with pytest.raises(OSError, match="snapshot disk full"):
        await service.process("doc-1")

    embedder.embed_documents.assert_not_awaited()
    repository.replace_chunks.assert_not_awaited()
    vector_store.delete_by_document.assert_not_awaited()
    vector_store.upsert.assert_not_awaited()
    started_statuses = [call.args[1] for call in repository.update_document_status.await_args_list]
    assert started_statuses == [DocumentStatus.PARSING, DocumentStatus.CHUNKING]
