"""验证后台 Worker 的任务提交、不可重试错误和清理语义。"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest

from ultimate_rag.application import DocumentProcessingService
from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import IngestionJob, IngestionJobStatus
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.worker import IngestionWorker


def _job() -> IngestionJob:
    """构造一次已经由测试 Worker 领取的任务快照。"""

    now = datetime.now(UTC)
    return IngestionJob(
        id="job-1",
        document_id="doc-1",
        status=IngestionJobStatus.RUNNING,
        attempts=1,
        max_attempts=3,
        available_at=now,
        locked_at=now,
        worker_id="worker-1",
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _worker(repository: AsyncMock, processor: AsyncMock) -> IngestionWorker:
    """以长心跳间隔构造单次测试 Worker，避免测试产生真实等待。"""

    return IngestionWorker(
        repository=cast(Repository, repository),
        processor=cast(DocumentProcessingService, processor),
        worker_id="worker-1",
        poll_interval=0.1,
        lease_seconds=900,
        heartbeat_seconds=300,
        retry_delay_seconds=10,
    )


@pytest.mark.asyncio
async def test_worker_completes_job_after_document_is_ready() -> None:
    """处理服务成功后才可提交任务完成，且不应执行失败清理。"""

    repository = AsyncMock()
    repository.claim_ingestion_job.return_value = _job()
    processor = AsyncMock()

    assert await _worker(repository, processor).run_once() is True

    processor.process.assert_awaited_once_with("doc-1")
    repository.complete_ingestion_job.assert_awaited_once_with("job-1", "worker-1")
    processor.cleanup_partial_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_does_not_retry_invalid_document() -> None:
    """损坏或非法文件不会因重试而变好，应清理半成品并直接进入终态失败。"""

    repository = AsyncMock()
    repository.claim_ingestion_job.return_value = _job()
    repository.fail_ingestion_job.return_value = False
    processor = AsyncMock()
    processor.process.side_effect = InvalidDocumentError("PDF 文件损坏")

    assert await _worker(repository, processor).run_once() is True

    processor.cleanup_partial_index.assert_awaited_once_with("doc-1")
    repository.fail_ingestion_job.assert_awaited_once_with(
        "job-1",
        "worker-1",
        error_message="PDF 文件损坏",
        retryable=False,
        retry_delay_seconds=10,
    )
