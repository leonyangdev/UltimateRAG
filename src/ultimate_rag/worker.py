"""PostgreSQL 持久化摄取任务 Worker 进程。

Worker 与 FastAPI 完全分离：上传请求只入队，本文进程循环领取任务并执行耗时处理。任务领取使用
``FOR UPDATE SKIP LOCKED``，长任务通过心跳续租，进程崩溃后由其他 Worker 回收过期租约。
无 Redis/Kafka 也能满足当前 V2 的可靠异步需求，并保留以后替换任务基础设施的清晰边界。
"""

import asyncio
import logging
import os
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from ultimate_rag.application import DocumentProcessingService
from ultimate_rag.config import Settings, get_settings
from ultimate_rag.domain.exceptions import InvalidDocumentError, ResourceNotFoundError
from ultimate_rag.domain.models import IngestionJob
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.runtime import create_processing_runtime

logger = logging.getLogger(__name__)


class IngestionWorker:
    """领取、续租、处理并提交文档任务的单并发 Worker。"""

    def __init__(
        self,
        *,
        repository: Repository,
        processor: DocumentProcessingService,
        worker_id: str,
        poll_interval: float,
        lease_seconds: int,
        heartbeat_seconds: int,
        retry_delay_seconds: int,
    ) -> None:
        """注入任务事实端、处理服务与全部有界时间参数。"""

        self._repository = repository
        self._processor = processor
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._retry_delay_seconds = retry_delay_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        """持续处理任务直到收到进程停止信号。"""

        logger.info("Ingestion worker started", extra={"worker_id": self._worker_id})
        while not stop_event.is_set():
            processed = await self.run_once()
            if processed:
                continue
            # wait_for 同时提供低延迟停止和空队列退避，避免无任务时频繁查询 PostgreSQL。
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
        logger.info("Ingestion worker stopped", extra={"worker_id": self._worker_id})

    async def run_once(self) -> bool:
        """领取并处理至多一个任务；空队列返回 ``False``。"""

        job = await self._repository.claim_ingestion_job(
            self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if job is None:
            return False

        logger.info(
            "Ingestion job claimed",
            extra={
                "worker_id": self._worker_id,
                "document_id": job.document_id,
                "job_id": job.id,
                "attempt": job.attempts,
            },
        )
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job, lease_lost))
        try:
            await self._processor.process(job.document_id)
        except asyncio.CancelledError:
            # 进程退出时不篡改任务状态；租约到期后其他 Worker 会安全回收并重跑幂等管线。
            raise
        except Exception as exc:
            await self._handle_processing_failure(job, exc, lease_lost.is_set())
        else:
            if lease_lost.is_set():
                logger.error(
                    "Ingestion completed after lease was lost; another worker will reconcile it",
                    extra={"document_id": job.document_id, "job_id": job.id},
                )
            else:
                await self._repository.complete_ingestion_job(job.id, self._worker_id)
                logger.info(
                    "Ingestion job completed",
                    extra={"document_id": job.document_id, "job_id": job.id},
                )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _heartbeat(self, job: IngestionJob, lease_lost: asyncio.Event) -> None:
        """在处理期间定期续租，所有权丢失后通知主任务停止提交状态。"""

        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                renewed = await self._repository.heartbeat_ingestion_job(job.id, self._worker_id)
            except Exception:
                # 数据库不可达时无法证明仍拥有租约，按租约丢失处理，禁止随后提交状态。
                lease_lost.set()
                logger.exception(
                    "Ingestion job heartbeat failed",
                    extra={"document_id": job.document_id, "job_id": job.id},
                )
                return
            if renewed:
                continue
            lease_lost.set()
            logger.error(
                "Ingestion job lease lost",
                extra={"document_id": job.document_id, "job_id": job.id},
            )
            return

    async def _handle_processing_failure(
        self,
        job: IngestionJob,
        exc: Exception,
        lease_lost: bool,
    ) -> None:
        """清理半成品向量，并按错误类型提交有限重试或终态失败。"""

        logger.exception(
            "Ingestion job failed",
            extra={
                "document_id": job.document_id,
                "job_id": job.id,
                "attempt": job.attempts,
            },
        )
        if lease_lost:
            # 失去所有权后不能覆盖新 Worker 的状态，也不能删除它可能已经写入的向量。
            return

        try:
            await self._processor.cleanup_partial_index(job.document_id)
        except Exception:
            # 清理失败会完整记录，但原处理异常仍用于任务重试决策；下一次索引阶段会再次按
            # document_id 删除旧向量，READY 过滤也会阻止半成品被检索。
            logger.exception(
                "Failed to clean partial vector index",
                extra={"document_id": job.document_id, "job_id": job.id},
            )

        retryable = not isinstance(exc, (InvalidDocumentError, ResourceNotFoundError))
        # 指数退避只作用于剩余的有限尝试，且封顶五分钟，避免外部服务故障时形成请求风暴。
        retry_delay = min(self._retry_delay_seconds * (2 ** max(job.attempts - 1, 0)), 300)
        message = (str(exc).strip() or exc.__class__.__name__)[:1000]
        will_retry = await self._repository.fail_ingestion_job(
            job.id,
            self._worker_id,
            error_message=message,
            retryable=retryable,
            retry_delay_seconds=retry_delay,
        )
        logger.info(
            "Ingestion failure persisted",
            extra={
                "document_id": job.document_id,
                "job_id": job.id,
                "will_retry": will_retry,
            },
        )


def _worker_id() -> str:
    """生成便于日志定位且跨进程唯一的短 Worker 标识。"""

    return f"{socket.gethostname()}:{os.getpid()}:{str(uuid4())[:8]}"


async def run_worker(settings: Settings) -> None:
    """装配运行时、注册优雅停止信号并启动 Worker 循环。"""

    runtime = create_processing_runtime(settings)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            # Windows Proactor Loop 不支持 add_signal_handler，本地 Ctrl+C 仍由 asyncio.run 处理。
            pass

    try:
        await runtime.initialize()
        worker = IngestionWorker(
            repository=runtime.repository,
            processor=runtime.processor,
            worker_id=_worker_id(),
            poll_interval=settings.worker_poll_interval_seconds,
            lease_seconds=settings.worker_lease_seconds,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            retry_delay_seconds=settings.worker_retry_delay_seconds,
        )
        await worker.run(stop_event)
    finally:
        await runtime.close()


def main() -> None:
    """配置结构化基础日志并运行独立 Worker 进程。"""

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
