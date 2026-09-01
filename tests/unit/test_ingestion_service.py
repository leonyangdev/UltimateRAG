"""验证文档在访问对象存储前完成不可信上传输入校验。"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import IngestionService
from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.ports import ObjectStorage
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.parsers import MarkdownParser, ParserRegistry


@pytest.mark.asyncio
async def test_ingestion_rejects_extension_mime_mismatch_before_storage() -> None:
    """扩展名与 MIME 没有任何 Parser 同时支持时，不应写入 MinIO。"""

    repository = AsyncMock()
    repository.get_knowledge_base.return_value = object()
    storage = AsyncMock()
    service = IngestionService(
        repository=cast(Repository, repository),
        storage=cast(ObjectStorage, storage),
        parser_registry=ParserRegistry([MarkdownParser()]),
        max_upload_bytes=1024,
        job_max_attempts=3,
    )

    with pytest.raises(InvalidDocumentError, match="不支持的文档类型"):
        await service.ingest(
            knowledge_base_id="kb-1",
            filename="disguised.md",
            mime_type="image/png",
            content=b"not an image",
        )

    storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_requeues_existing_document_with_configured_attempt_limit() -> None:
    """存量回填应只重置后台任务，不重新上传或复制原始文件。"""

    repository = AsyncMock()
    repository.requeue_document.return_value = object()
    storage = AsyncMock()
    service = IngestionService(
        repository=cast(Repository, repository),
        storage=cast(ObjectStorage, storage),
        parser_registry=ParserRegistry([MarkdownParser()]),
        max_upload_bytes=1024,
        job_max_attempts=4,
    )

    result = await service.reindex("document-1")

    assert result is repository.requeue_document.return_value
    repository.requeue_document.assert_awaited_once_with("document-1", max_attempts=4)
    storage.put.assert_not_awaited()
