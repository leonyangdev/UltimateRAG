"""面向业务语义的 PostgreSQL Repository。

Repository 负责事务边界和 ORM/领域模型映射，不编排 MinIO、Milvus 或模型服务调用。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ultimate_rag.domain.exceptions import ResourceNotFoundError
from ultimate_rag.domain.models import (
    Chunk,
    Document,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    JsonValue,
    KnowledgeBase,
    SourceLocator,
)
from ultimate_rag.infrastructure.database.models import (
    ChunkModel,
    DocumentModel,
    IngestionJobModel,
    KnowledgeBaseModel,
)


class Repository:
    """封装知识库、文档和 Chunk 的数据库事实读写。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """接收可复用 Session 工厂，每个公共操作自行定义短事务。"""
        self._session_factory = session_factory

    async def create_knowledge_base(self, name: str, description: str) -> KnowledgeBase:
        """创建知识库并返回数据库生成时间已填充的领域对象。"""
        model = KnowledgeBaseModel(id=str(uuid4()), name=name, description=description)
        async with self._session_factory() as session, session.begin():
            session.add(model)
        return self._knowledge_base(model)

    async def list_knowledge_bases(self) -> list[KnowledgeBase]:
        """按创建时间倒序返回全部知识库。"""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(KnowledgeBaseModel).order_by(KnowledgeBaseModel.created_at.desc())
            )
            return [self._knowledge_base(model) for model in result]

    async def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBase:
        """按 ID 读取知识库，不存在时抛出 ``ResourceNotFoundError``。"""
        async with self._session_factory() as session:
            model = await session.get(KnowledgeBaseModel, knowledge_base_id)
            if model is None:
                raise ResourceNotFoundError("知识库不存在")
            return self._knowledge_base(model)

    async def delete_knowledge_base(self, knowledge_base_id: str) -> list[Document]:
        """在单个事务中删除知识库及级联记录，并返回原文档快照。"""
        async with self._session_factory() as session, session.begin():
            model = await session.get(KnowledgeBaseModel, knowledge_base_id)
            if model is None:
                raise ResourceNotFoundError("知识库不存在")

            # 删除前获取领域快照。数据库提交后 ORM 实例可能失效，而应用服务仍需要文档的
            # Object Key 清理 MinIO；返回领域对象可以避免把 SQLAlchemy 生命周期泄漏到上层。
            document_models = list(
                await session.scalars(
                    select(DocumentModel).where(
                        DocumentModel.knowledge_base_id == knowledge_base_id
                    )
                )
            )
            documents = [self._document(document) for document in document_models]

            # ORM 关系配置负责级联删除 Document 和 Chunk，三类事实记录在同一事务中提交。
            await session.delete(model)
            return documents

    async def create_document(
        self,
        *,
        document_id: str,
        knowledge_base_id: str,
        filename: str,
        mime_type: str,
        extension: str,
        object_key: str,
        sha256: str,
    ) -> Document:
        """在确认所属知识库存在后创建 ``PENDING`` 文档记录。"""
        # PENDING 是事实记录的初始状态。应用服务只有在后续 Parse、Chunk、Embed、Index
        # 全部完成后才会推进到 READY，因此刚上传的文档不会提前参与检索。
        model = DocumentModel(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            mime_type=mime_type,
            extension=extension,
            object_key=object_key,
            sha256=sha256,
            status=DocumentStatus.PENDING.value,
        )
        async with self._session_factory() as session, session.begin():
            # 即使应用服务已经检查过知识库，这里仍在创建事务内验证一次。
            # 它既让 Repository 的公共方法可以独立安全调用，也能处理检查后知识库被删除的竞态。
            if await session.get(KnowledgeBaseModel, knowledge_base_id) is None:
                raise ResourceNotFoundError("知识库不存在")
            session.add(model)
        return self._document(model)

    async def create_document_with_job(
        self,
        *,
        document_id: str,
        knowledge_base_id: str,
        filename: str,
        mime_type: str,
        extension: str,
        object_key: str,
        sha256: str,
        max_attempts: int,
    ) -> Document:
        """在一个事务内创建 ``PENDING`` 文档和唯一摄取任务。

        文档事实与任务必须原子提交。若先创建文档、后创建任务时进程退出，用户会看到一个
        永远停留在 ``PENDING`` 的文档；同一事务可以从根源上消除这个丢任务窗口。
        """

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        now = datetime.now(UTC)
        document = DocumentModel(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            mime_type=mime_type,
            extension=extension,
            object_key=object_key,
            sha256=sha256,
            status=DocumentStatus.PENDING.value,
        )
        job = IngestionJobModel(
            id=str(uuid4()),
            document_id=document_id,
            status=IngestionJobStatus.PENDING.value,
            attempts=0,
            max_attempts=max_attempts,
            available_at=now,
        )
        async with self._session_factory() as session, session.begin():
            if await session.get(KnowledgeBaseModel, knowledge_base_id) is None:
                raise ResourceNotFoundError("知识库不存在")
            session.add_all([document, job])
        return self._document(document)

    async def claim_ingestion_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> IngestionJob | None:
        """以行锁跳过其他 Worker 正在领取的任务，并回收租约过期任务。

        ``FOR UPDATE SKIP LOCKED`` 只用于队列表这一明确场景：多个 Worker 可以并行领取，
        但同一事务瞬间只有一个 Worker 能修改某行。``locked_at`` 租约让进程崩溃后的
        ``RUNNING`` 任务重新可见，不需要人工修改数据库。
        """

        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")

        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=lease_seconds)
        async with self._session_factory() as session, session.begin():
            # 最后一次尝试若随进程崩溃而租约过期，普通领取条件不会再选中。先在短事务内
            # 对这种行做终态收敛；若文档已 READY，说明只是“任务完成提交”窗口崩溃，任务可
            # 直接成功，否则明确 FAILED，不能永久留在 RUNNING。
            exhausted_statement = (
                select(IngestionJobModel)
                .where(
                    IngestionJobModel.status == IngestionJobStatus.RUNNING.value,
                    IngestionJobModel.locked_at.is_not(None),
                    IngestionJobModel.locked_at <= stale_before,
                    IngestionJobModel.attempts >= IngestionJobModel.max_attempts,
                )
                .order_by(IngestionJobModel.locked_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            exhausted = await session.scalar(exhausted_statement)
            if exhausted is not None:
                document = await session.get(DocumentModel, exhausted.document_id)
                is_ready = document is not None and document.status == DocumentStatus.READY.value
                exhausted.status = (
                    IngestionJobStatus.SUCCEEDED.value
                    if is_ready
                    else IngestionJobStatus.FAILED.value
                )
                exhausted.locked_at = None
                exhausted.worker_id = None
                if not is_ready:
                    message = "Worker 租约过期且已达到最大尝试次数"
                    exhausted.error_message = message
                    if document is not None:
                        document.status = DocumentStatus.FAILED.value
                        document.error_message = message
                return None

            statement = (
                select(IngestionJobModel)
                .where(
                    IngestionJobModel.attempts < IngestionJobModel.max_attempts,
                    or_(
                        and_(
                            IngestionJobModel.status == IngestionJobStatus.PENDING.value,
                            IngestionJobModel.available_at <= now,
                        ),
                        and_(
                            IngestionJobModel.status == IngestionJobStatus.RUNNING.value,
                            IngestionJobModel.locked_at.is_not(None),
                            IngestionJobModel.locked_at <= stale_before,
                        ),
                    ),
                )
                .order_by(IngestionJobModel.available_at, IngestionJobModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            model = await session.scalar(statement)
            if model is None:
                return None

            model.status = IngestionJobStatus.RUNNING.value
            model.attempts += 1
            model.locked_at = now
            model.worker_id = worker_id
            model.error_message = None
            return self._ingestion_job(model)

    async def heartbeat_ingestion_job(self, job_id: str, worker_id: str) -> bool:
        """续租当前 Worker 拥有的运行中任务；所有权变化时返回 ``False``。"""

        async with self._session_factory() as session, session.begin():
            model = await session.get(IngestionJobModel, job_id)
            if (
                model is None
                or model.status != IngestionJobStatus.RUNNING.value
                or model.worker_id != worker_id
            ):
                return False
            model.locked_at = datetime.now(UTC)
            return True

    async def complete_ingestion_job(self, job_id: str, worker_id: str) -> None:
        """在文档已经 ``READY`` 后，把当前租约拥有者的任务标记为完成。"""

        async with self._session_factory() as session, session.begin():
            model = await session.get(IngestionJobModel, job_id)
            if (
                model is None
                or model.status != IngestionJobStatus.RUNNING.value
                or model.worker_id != worker_id
            ):
                raise RuntimeError("摄取任务租约已丢失，不能提交完成状态")
            document = await session.get(DocumentModel, model.document_id)
            if document is None or document.status != DocumentStatus.READY.value:
                raise RuntimeError("文档尚未 READY，不能提交摄取任务")
            model.status = IngestionJobStatus.SUCCEEDED.value
            model.locked_at = None
            model.worker_id = None
            model.error_message = None

    async def fail_ingestion_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int,
    ) -> bool:
        """记录一次失败，并返回任务是否已安排有限重试。

        参数错误和损坏文件直接终止；临时网络/服务故障仅在剩余尝试次数内重试。
        文档与任务状态在同一 PostgreSQL 事务更新，前端不会观察到相互矛盾的状态。
        """

        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        safe_error = (error_message.strip() or "Unknown ingestion error")[:1000]
        async with self._session_factory() as session, session.begin():
            model = await session.get(IngestionJobModel, job_id)
            if (
                model is None
                or model.status != IngestionJobStatus.RUNNING.value
                or model.worker_id != worker_id
            ):
                raise RuntimeError("摄取任务租约已丢失，不能提交失败状态")
            document = await session.get(DocumentModel, model.document_id)
            if document is None:
                raise ResourceNotFoundError("文档不存在")

            should_retry = retryable and model.attempts < model.max_attempts
            model.error_message = safe_error
            model.locked_at = None
            model.worker_id = None
            if should_retry:
                model.status = IngestionJobStatus.PENDING.value
                model.available_at = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
                document.status = DocumentStatus.PENDING.value
                document.error_message = (
                    f"第 {model.attempts}/{model.max_attempts} 次处理暂时失败，"
                    f"系统将自动重试：{safe_error}"
                )[:1000]
            else:
                model.status = IngestionJobStatus.FAILED.value
                document.status = DocumentStatus.FAILED.value
                document.error_message = safe_error
            return should_retry

    async def list_documents(self, knowledge_base_id: str) -> list[Document]:
        """返回知识库文档；知识库不存在与空知识库使用不同语义。"""
        # 先显式读取知识库，确保“不存在”抛出 404，而真实存在但没有文档时返回空列表。
        # 如果只执行下面的 Document 查询，这两种业务情况都会得到相同的空结果。
        await self.get_knowledge_base(knowledge_base_id)
        async with self._session_factory() as session:
            result = await session.scalars(
                select(DocumentModel)
                .where(DocumentModel.knowledge_base_id == knowledge_base_id)
                .order_by(DocumentModel.created_at.desc())
            )
            return [self._document(model) for model in result]

    async def get_document(self, document_id: str) -> Document:
        """按 ID 读取文档，不存在时抛出 ``ResourceNotFoundError``。"""
        async with self._session_factory() as session:
            model = await session.get(DocumentModel, document_id)
            if model is None:
                raise ResourceNotFoundError("文档不存在")
            return self._document(model)

    async def list_ready_document_ids(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str] = (),
    ) -> set[str]:
        """返回知识库内允许参与检索且满足显式文档过滤的 ID 集合。

        PostgreSQL 是文档状态事实来源。调用方即使传入其他知识库、正在处理或已失败的文档 ID，
        也只会得到当前知识库中 ``READY`` 的交集，不能依靠客户端过滤跨越业务边界。
        """

        async with self._session_factory() as session:
            statement = select(DocumentModel.id).where(
                DocumentModel.knowledge_base_id == knowledge_base_id,
                DocumentModel.status == DocumentStatus.READY.value,
            )
            if document_ids:
                statement = statement.where(DocumentModel.id.in_(document_ids))
            result = await session.scalars(statement)
            return set(result)

    async def get_chunks_with_neighbors(
        self,
        chunk_ids: Sequence[str],
        *,
        window: int,
    ) -> dict[str, list[Chunk]]:
        """批量读取命中 Chunk 及其有界相邻 Chunk，避免 Parent 扩展产生 N+1。

        本方法只按文档和顺序读取事实，不决定哪些邻居属于同一语义 Parent。应用层会继续依据
        ``parent_id`` 或旧数据的标题/来源边界过滤，并执行 Token 预算。返回字典保留每个命中
        Chunk 的独立窗口，同一邻居可被多个命中共享。
        """

        if window < 0 or window > 3:
            raise ValueError("window must be between 0 and 3")
        unique_ids = tuple(dict.fromkeys(chunk_ids))
        if not unique_ids:
            return {}

        async with self._session_factory() as session:
            matched_rows = list(
                await session.execute(
                    select(ChunkModel, DocumentModel.knowledge_base_id, DocumentModel.filename)
                    .join(DocumentModel, DocumentModel.id == ChunkModel.document_id)
                    .where(ChunkModel.id.in_(unique_ids))
                )
            )
            matched = {
                model.id: self._chunk(model, knowledge_base_id, filename)
                for model, knowledge_base_id, filename in matched_rows
            }
            if not matched:
                return {}

            # 每个命中只展开最多 ``2 * window + 1`` 个顺序位置。条件在一条 SQL 中合并，
            # top_k 最大 20、window 最大 3，因此不会生成无界 OR 或为每个 Hit 单独查询。
            conditions = [
                and_(
                    ChunkModel.document_id == chunk.document_id,
                    ChunkModel.chunk_index >= max(0, chunk.index - window),
                    ChunkModel.chunk_index <= chunk.index + window,
                )
                for chunk in matched.values()
            ]
            neighbor_rows = list(
                await session.execute(
                    select(ChunkModel, DocumentModel.knowledge_base_id, DocumentModel.filename)
                    .join(DocumentModel, DocumentModel.id == ChunkModel.document_id)
                    .where(or_(*conditions))
                    .order_by(ChunkModel.document_id, ChunkModel.chunk_index)
                )
            )
            neighbors = [
                self._chunk(model, knowledge_base_id, filename)
                for model, knowledge_base_id, filename in neighbor_rows
            ]

        return {
            chunk_id: [
                neighbor
                for neighbor in neighbors
                if neighbor.document_id == chunk.document_id
                and abs(neighbor.index - chunk.index) <= window
            ]
            for chunk_id, chunk in matched.items()
        }

    async def list_ready_chunks_page(
        self,
        *,
        after_chunk_id: str | None = None,
        limit: int = 500,
        knowledge_base_id: str | None = None,
    ) -> list[Chunk]:
        """按稳定 Chunk ID 分页读取可回填派生索引的事实数据。

        Keyset Pagination 不会随着页数增长产生 OFFSET 扫描；脚本在每批 Sparse Upsert 成功后
        才推进游标，失败时可从上一稳定 ID 重新执行，Upsert 的稳定主键保证幂等。
        """

        if not 1 <= limit <= 2000:
            raise ValueError("limit must be between 1 and 2000")
        statement = (
            select(ChunkModel, DocumentModel.knowledge_base_id, DocumentModel.filename)
            .join(DocumentModel, DocumentModel.id == ChunkModel.document_id)
            .where(DocumentModel.status == DocumentStatus.READY.value)
            .order_by(ChunkModel.id)
            .limit(limit)
        )
        if after_chunk_id is not None:
            statement = statement.where(ChunkModel.id > after_chunk_id)
        if knowledge_base_id is not None:
            statement = statement.where(DocumentModel.knowledge_base_id == knowledge_base_id)

        async with self._session_factory() as session:
            rows = list(await session.execute(statement))
        return [
            self._chunk(model, row_knowledge_base_id, filename)
            for model, row_knowledge_base_id, filename in rows
        ]

    async def update_document_status(
        self,
        document_id: str,
        status: DocumentStatus,
        *,
        parser_name: str | None = None,
        parser_version: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """原子更新文档处理状态及可选解析器/错误信息。"""
        async with self._session_factory() as session, session.begin():
            model = await session.get(DocumentModel, document_id)
            if model is None:
                raise ResourceNotFoundError("文档不存在")

            # 状态与错误信息在同一事务内更新。成功推进到新阶段时 error_message 默认清空，
            # 避免一次失败重试成功后仍向用户展示已经过期的错误原因。
            model.status = status.value
            model.error_message = error_message

            # Parser 信息只在解析器已确定时传入；后续状态更新不能用 None 覆盖已记录的版本，
            # 否则失败排查和重新构建索引时会失去原处理器信息。
            if parser_name is not None:
                model.parser_name = parser_name
            if parser_version is not None:
                model.parser_version = parser_version

    async def replace_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """在事务中替换文档 Chunk，保证重试不会产生重复事实记录。"""
        async with self._session_factory() as session, session.begin():
            # “先删后插”位于同一数据库事务：任意新 Chunk 写入失败都会整体回滚，
            # 不会让文档在 PostgreSQL 中只剩半套新 Chunk。稳定 ID 还保证成功重试结果一致。
            await session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
            session.add_all(
                [
                    ChunkModel(
                        id=chunk.id,
                        document_id=chunk.document_id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        heading_path=list(chunk.heading_path),
                        token_count=chunk.token_count,
                        chunk_metadata=dict(chunk.metadata),
                    )
                    for chunk in chunks
                ]
            )

    async def delete_document(self, document_id: str) -> Document:
        """删除文档及其级联 Chunk，并返回删除前领域快照。"""
        async with self._session_factory() as session, session.begin():
            model = await session.get(DocumentModel, document_id)
            if model is None:
                raise ResourceNotFoundError("文档不存在")
            document = self._document(model)
            await session.delete(model)
            return document

    @staticmethod
    def _knowledge_base(model: KnowledgeBaseModel) -> KnowledgeBase:
        """把 ORM 知识库映射为不可变领域对象。"""
        return KnowledgeBase(
            id=model.id,
            name=model.name,
            description=model.description,
            created_at=Repository._datetime(model.created_at),
            updated_at=Repository._datetime(model.updated_at),
        )

    @staticmethod
    def _document(model: DocumentModel) -> Document:
        """把 ORM 文档映射为带强类型状态的领域对象。"""
        return Document(
            id=model.id,
            knowledge_base_id=model.knowledge_base_id,
            filename=model.filename,
            mime_type=model.mime_type,
            extension=model.extension,
            object_key=model.object_key,
            sha256=model.sha256,
            status=DocumentStatus(model.status),
            parser_name=model.parser_name,
            parser_version=model.parser_version,
            error_message=model.error_message,
            created_at=Repository._datetime(model.created_at),
            updated_at=Repository._datetime(model.updated_at),
        )

    @staticmethod
    def _chunk(model: ChunkModel, knowledge_base_id: str, filename: str) -> Chunk:
        """把 Chunk ORM 事实和所属文档字段恢复为完整领域对象。"""

        # JSONB 来自本应用写入，但数据库仍是外部边界。复制后只把通过 JSON 类型约束的值
        # 传入领域模型，并用 Document 表中的事实文件名覆盖可能缺失的旧版 metadata。
        metadata = cast(dict[str, JsonValue], dict(model.chunk_metadata or {}))
        metadata["filename"] = filename
        raw_locator = metadata.get("source_locator")
        locator = (
            SourceLocator.from_metadata(cast(dict[str, object], raw_locator))
            if isinstance(raw_locator, dict)
            else SourceLocator(heading_path=tuple(model.heading_path or []))
        )
        return Chunk(
            id=model.id,
            knowledge_base_id=knowledge_base_id,
            document_id=model.document_id,
            index=model.chunk_index,
            content=model.content,
            heading_path=tuple(model.heading_path or []),
            token_count=model.token_count,
            locator=locator,
            metadata=metadata,
        )

    @staticmethod
    def _ingestion_job(model: IngestionJobModel) -> IngestionJob:
        """把 ORM 任务映射为 Worker 可安全持有的不可变快照。"""

        return IngestionJob(
            id=model.id,
            document_id=model.document_id,
            status=IngestionJobStatus(model.status),
            attempts=model.attempts,
            max_attempts=model.max_attempts,
            available_at=Repository._datetime(model.available_at),
            locked_at=model.locked_at,
            worker_id=model.worker_id,
            error_message=model.error_message,
            created_at=Repository._datetime(model.created_at),
            updated_at=Repository._datetime(model.updated_at),
        )

    @staticmethod
    def _datetime(value: datetime | None) -> datetime:
        """断言数据库默认时间已回填，避免构造不完整领域对象。"""
        if value is None:
            raise RuntimeError("Database did not populate timestamp")
        return value
