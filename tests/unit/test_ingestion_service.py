"""验证文档在访问对象存储前完成不可信上传输入校验。"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import IngestionService
from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.ports import Chunker, Embedder, ObjectStorage, VectorStore
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.parsers import MarkdownParser, ParserRegistry


@pytest.mark.asyncio
async def test_ingestion_rejects_non_markdown_mime_before_storage() -> None:
    """明显非文本 MIME 即使使用 .md 后缀，也不应写入 MinIO。"""

    repository = AsyncMock()
    repository.get_knowledge_base.return_value = object()
    storage = AsyncMock()
    service = IngestionService(
        repository=cast(Repository, repository),
        storage=cast(ObjectStorage, storage),
        parser_registry=ParserRegistry([MarkdownParser()]),
        chunker=cast(Chunker, AsyncMock()),
        embedder=cast(Embedder, AsyncMock()),
        vector_store=cast(VectorStore, AsyncMock()),
        max_upload_bytes=1024,
    )

    with pytest.raises(InvalidDocumentError, match="MIME"):
        await service.ingest(
            knowledge_base_id="kb-1",
            filename="disguised.md",
            mime_type="image/png",
            content=b"not an image",
        )

    storage.put.assert_not_awaited()
