"""验证图片 Asset 的跨存储持久化与会话证据快照。"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import DocumentProcessingService
from ultimate_rag.domain.models import (
    BlockType,
    ChatEvidence,
    Citation,
    Document,
    DocumentAsset,
    DocumentStatus,
    ParsedAsset,
    RetrievalMode,
    RetrievalResult,
    RetrievalTrace,
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


def _document() -> Document:
    """构造 READY 前处理中的 PDF 文档事实。"""

    now = datetime.now(UTC)
    return Document(
        id="doc-1",
        knowledge_base_id="kb-1",
        filename="paper.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        object_key="kb-1/doc-1/source.pdf",
        sha256="source-sha",
        status=DocumentStatus.CHUNKING,
        parser_name="pdf-docling",
        parser_version="3.0",
        error_message=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_processing_service_persists_asset_with_stable_object_key() -> None:
    """摄取重试应覆盖同一 MinIO Key，并把哈希后的元数据交给单库事务。"""

    repository = AsyncMock()
    repository.list_document_assets.return_value = []
    storage = AsyncMock()
    service = DocumentProcessingService(
        repository=cast(Repository, repository),
        storage=cast(ObjectStorage, storage),
        parser_registry=cast(ParserRegistry, object()),
        chunker=cast(Chunker, object()),
        chunk_snapshot_store=cast(ChunkSnapshotStore, object()),
        embedder=cast(Embedder, object()),
        vector_store=cast(VectorStore, object()),
    )
    parsed = ParsedAsset(
        id="asset-1",
        block_id="block-1",
        kind=BlockType.IMAGE,
        media_type="image/jpeg",
        filename="asset-1.jpg",
        title="Transformer 架构图",
        description="Encoder 与 Decoder",
        content=b"jpeg-bytes",
        locator=SourceLocator(page=3, bbox=(10, 20, 300, 400)),
    )

    await service._persist_assets(_document(), (parsed,))

    expected_key = "kb-1/doc-1/assets/asset-1.jpg"
    storage.put.assert_awaited_once_with(expected_key, b"jpeg-bytes", "image/jpeg")
    persisted = repository.replace_document_assets.await_args.args[1]
    assert persisted[0].object_key == expected_key
    assert persisted[0].block_id == "block-1"
    assert len(persisted[0].sha256) == 64


def test_chat_evidence_json_round_trip_keeps_assets_and_citation_mapping() -> None:
    """历史消息刷新后必须仍能解析 asset:// ID 与 [来源 N] 对应的完整证据。"""

    asset = DocumentAsset(
        id="asset-1",
        document_id="doc-1",
        block_id="block-1",
        kind=BlockType.IMAGE,
        object_key="kb-1/doc-1/assets/asset-1.jpg",
        media_type="image/jpeg",
        filename="asset-1.jpg",
        title="Transformer 架构图",
        description="Encoder 与 Decoder",
        sha256="abc",
        locator=SourceLocator(page=3),
    )
    result = RetrievalResult(
        chunk_id="chunk-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="paper.pdf",
        content="![Transformer 架构图](asset://asset-1)",
        heading_path=("Model Architecture",),
        score=0.92,
        locator=SourceLocator(page=3),
        content_types=(BlockType.IMAGE,),
        assets=(asset,),
    )
    trace = RetrievalTrace(
        original_query="展示 Transformer 架构图",
        query_variants=("展示 Transformer 架构图",),
        mode=RetrievalMode.HYBRID,
        candidate_count=5,
        result_count=1,
        rewrite_applied=False,
        rerank_applied=True,
        parent_expansion_applied=False,
    )
    evidence = ChatEvidence(
        citations=(Citation("doc-1", "paper.pdf", "chunk-1", ("Model Architecture",)),),
        results=(result,),
        trace=trace,
    )

    metadata = Repository._chat_evidence_metadata(evidence)
    restored = Repository._chat_evidence(cast(dict[str, object], metadata))

    assert restored is not None
    assert restored.citations[0].chunk_id == "chunk-1"
    assert restored.results[0].assets[0].id == "asset-1"
    assert restored.results[0].assets[0].object_key == asset.object_key
    assert restored.trace.rerank_applied is True
