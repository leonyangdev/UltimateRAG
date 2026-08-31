"""面向业务语义的 PostgreSQL Repository。

Repository 负责事务边界和 ORM/领域模型映射，不编排 MinIO、Milvus 或模型服务调用。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ultimate_rag.domain.exceptions import (
    ChatSessionBusyError,
    DocumentBusyError,
    ResourceNotFoundError,
)
from ultimate_rag.domain.models import (
    BlockType,
    ChatEvidence,
    ChatMessage,
    ChatMessageStatus,
    ChatRole,
    ChatSession,
    ChatTurn,
    Chunk,
    Citation,
    Document,
    DocumentAsset,
    DocumentStatus,
    IngestionJob,
    IngestionJobStatus,
    JsonValue,
    KnowledgeBase,
    RetrievalIntent,
    RetrievalMode,
    RetrievalResult,
    RetrievalTrace,
    SourceLocator,
)
from ultimate_rag.infrastructure.database.models import (
    ChatMessageModel,
    ChatSessionModel,
    ChunkModel,
    DocumentAssetModel,
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

    async def create_chat_session(self, knowledge_base_id: str) -> ChatSession:
        """为知识库创建一个空白会话；首次问题会确定可读标题。"""

        model = ChatSessionModel(id=str(uuid4()), knowledge_base_id=knowledge_base_id)
        async with self._session_factory() as session, session.begin():
            if await session.get(KnowledgeBaseModel, knowledge_base_id) is None:
                raise ResourceNotFoundError("知识库不存在")
            session.add(model)
        return self._chat_session(model)

    async def list_chat_sessions(self, knowledge_base_id: str) -> list[ChatSession]:
        """按最近活动时间列出历史会话，并区分知识库不存在与无会话。"""

        await self.get_knowledge_base(knowledge_base_id)
        async with self._session_factory() as session:
            values = await session.scalars(
                select(ChatSessionModel)
                .where(ChatSessionModel.knowledge_base_id == knowledge_base_id)
                .order_by(ChatSessionModel.updated_at.desc())
            )
            return [self._chat_session(model) for model in values]

    async def get_chat_session(self, session_id: str) -> ChatSession:
        """读取会话元数据，不存在时返回稳定的业务错误。"""

        async with self._session_factory() as session:
            model = await session.get(ChatSessionModel, session_id)
            if model is None:
                raise ResourceNotFoundError("会话不存在")
            return self._chat_session(model)

    async def list_chat_messages(self, session_id: str) -> list[ChatMessage]:
        """按序返回会话全部消息，包括可供前端解释的失败生成。"""

        await self.get_chat_session(session_id)
        async with self._session_factory() as session:
            values = await session.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.sequence)
            )
            return [self._chat_message(model) for model in values]

    async def begin_chat_turn(
        self,
        *,
        session_id: str,
        knowledge_base_id: str,
        question: str,
        stale_after_seconds: int,
    ) -> ChatTurn:
        """原子创建用户消息与待提交助手消息，并返回此前完整历史。

        会话行锁与 PENDING 助手占位共同保证同一会话最多只有一个生成。若进程崩溃，超过
        ``stale_after_seconds`` 的占位会被明确标记 FAILED 后恢复，避免会话永久锁死。
        """

        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question cannot be empty")
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=stale_after_seconds)
        async with self._session_factory() as session, session.begin():
            model = await session.scalar(
                select(ChatSessionModel).where(ChatSessionModel.id == session_id).with_for_update()
            )
            if model is None:
                raise ResourceNotFoundError("会话不存在")
            if model.knowledge_base_id != knowledge_base_id:
                raise ResourceNotFoundError("会话不属于当前知识库")

            pending = await session.scalar(
                select(ChatMessageModel)
                .where(
                    ChatMessageModel.session_id == session_id,
                    ChatMessageModel.role == ChatRole.ASSISTANT.value,
                    ChatMessageModel.status == ChatMessageStatus.PENDING.value,
                )
                .order_by(ChatMessageModel.sequence.desc())
                .limit(1)
            )
            if pending is not None and pending.updated_at > stale_before:
                raise ChatSessionBusyError("当前会话正在生成回答，请等待完成后再发送")
            if pending is not None:
                pending.status = ChatMessageStatus.FAILED.value
                pending.error_message = "上一次生成因服务中断未完成，请重新提问"
                pending.updated_at = now

            history_models = list(
                await session.scalars(
                    select(ChatMessageModel)
                    .where(
                        ChatMessageModel.session_id == session_id,
                        ChatMessageModel.status == ChatMessageStatus.COMPLETE.value,
                    )
                    .order_by(ChatMessageModel.sequence)
                )
            )
            user = ChatMessageModel(
                id=str(uuid4()),
                session_id=session_id,
                sequence=model.next_sequence,
                role=ChatRole.USER.value,
                status=ChatMessageStatus.COMPLETE.value,
                content=normalized_question,
                created_at=now,
                updated_at=now,
            )
            assistant = ChatMessageModel(
                id=str(uuid4()),
                session_id=session_id,
                sequence=model.next_sequence + 1,
                role=ChatRole.ASSISTANT.value,
                status=ChatMessageStatus.PENDING.value,
                content="",
                created_at=now,
                updated_at=now,
            )
            if model.next_sequence == 1:
                model.title = self._chat_title(normalized_question)
            model.next_sequence += 2
            model.updated_at = now
            session.add_all([user, assistant])

        return ChatTurn(
            session=self._chat_session(model),
            user_message=self._chat_message(user),
            assistant_message=self._chat_message(assistant),
            history=tuple(self._chat_message(value) for value in history_models),
        )

    async def complete_chat_turn(
        self,
        assistant_message_id: str,
        content: str,
        evidence: ChatEvidence | None = None,
    ) -> None:
        """仅允许把 PENDING 助手占位提交为非空答案与检索快照。

        ``evidence`` 与正文在同一事务提交。浏览器刷新后不能只恢复一句含 ``asset://`` 的
        Markdown、却丢失 Asset 与 Citation 映射，否则历史答案会出现无法渲染的资源占位。
        """

        normalized = content.strip()
        if not normalized:
            raise ValueError("assistant content cannot be empty")
        async with self._session_factory() as session, session.begin():
            message = await session.get(ChatMessageModel, assistant_message_id)
            if (
                message is None
                or message.role != ChatRole.ASSISTANT.value
                or message.status != ChatMessageStatus.PENDING.value
            ):
                raise RuntimeError("助手消息不存在或已经结束")
            message.content = normalized
            message.retrieval_evidence = cast(
                dict[str, object] | None,
                self._chat_evidence_metadata(evidence),
            )
            message.status = ChatMessageStatus.COMPLETE.value
            message.error_message = None
            parent = await session.get(ChatSessionModel, message.session_id)
            if parent is not None:
                parent.updated_at = datetime.now(UTC)

    async def fail_chat_turn(self, assistant_message_id: str, error_message: str) -> None:
        """把尚未完成的生成标记为 FAILED；重复调用保持幂等。"""

        async with self._session_factory() as session, session.begin():
            message = await session.get(ChatMessageModel, assistant_message_id)
            if message is None or message.status != ChatMessageStatus.PENDING.value:
                return
            message.status = ChatMessageStatus.FAILED.value
            message.error_message = (error_message.strip() or "生成失败")[:1000]

    async def update_chat_memory(
        self,
        session_id: str,
        *,
        summary: str,
        through_sequence: int,
    ) -> ChatSession:
        """更新可重建的递归摘要游标，不删除任何原始消息。"""

        async with self._session_factory() as session, session.begin():
            model = await session.scalar(
                select(ChatSessionModel).where(ChatSessionModel.id == session_id).with_for_update()
            )
            if model is None:
                raise ResourceNotFoundError("会话不存在")
            # 只允许游标前进；并发或重试不能用旧摘要覆盖更新后的长期记忆。
            if through_sequence > model.memory_through_sequence:
                model.memory_summary = summary.strip()
                model.memory_through_sequence = through_sequence
            return self._chat_session(model)

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

    async def requeue_document(self, document_id: str, *, max_attempts: int) -> Document:
        """把终态文档原子重置为 PENDING，并复用唯一摄取任务重建派生数据。

        原始文件和文档 ID 都保持不变，因此重新解析可以为存量 PDF 生成新的 Asset/Chunk，
        同时稳定覆盖该文档的 PostgreSQL 与 Milvus 派生索引。处理中状态会被明确拒绝，避免
        两个 Worker 同时替换同一文档事实。
        """

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            document = await session.scalar(
                select(DocumentModel).where(DocumentModel.id == document_id).with_for_update()
            )
            if document is None:
                raise ResourceNotFoundError("文档不存在")
            if document.status not in {
                DocumentStatus.READY.value,
                DocumentStatus.FAILED.value,
            }:
                raise DocumentBusyError("文档正在后台处理，不能重复提交重建")

            job = await session.scalar(
                select(IngestionJobModel)
                .where(IngestionJobModel.document_id == document_id)
                .with_for_update()
            )
            if job is None:
                job = IngestionJobModel(id=str(uuid4()), document_id=document_id)
                session.add(job)
            job.status = IngestionJobStatus.PENDING.value
            job.attempts = 0
            job.max_attempts = max_attempts
            job.available_at = now
            job.locked_at = None
            job.worker_id = None
            job.error_message = None

            document.status = DocumentStatus.PENDING.value
            document.error_message = None
            document.updated_at = now
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

    async def get_chunk(self, chunk_id: str) -> Chunk:
        """读取单个 Chunk 及其所属文档的知识库 ID、文件名事实。

        Chunk 表不冗余知识库与展示文件名，因此必须在同一条 SQL 中连接 Document 后再恢复完整
        领域对象。该查询用于 PDF 视觉证据等精确来源读取，不产生额外 N+1。

        Args:
            chunk_id: 稳定 Chunk 主键。

        Returns:
            已恢复 SourceLocator 和 Chunk metadata 的领域对象。

        Raises:
            ResourceNotFoundError: Chunk 不存在或所属 Document 已被级联删除。
        """

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ChunkModel, DocumentModel.knowledge_base_id, DocumentModel.filename)
                    .join(DocumentModel, DocumentModel.id == ChunkModel.document_id)
                    .where(ChunkModel.id == chunk_id)
                )
            ).one_or_none()
            if row is None:
                raise ResourceNotFoundError("文档片段不存在")
            model, knowledge_base_id, filename = row
            return self._chunk(model, knowledge_base_id, filename)

    async def get_document_asset(self, asset_id: str) -> DocumentAsset:
        """按稳定 ID 读取单个文档资源，不返回二进制内容。"""

        async with self._session_factory() as session:
            model = await session.get(DocumentAssetModel, asset_id)
            if model is None:
                raise ResourceNotFoundError("文档资源不存在")
            return self._document_asset(model)

    async def get_document_assets(
        self,
        asset_ids: Sequence[str],
    ) -> dict[str, DocumentAsset]:
        """一次性读取多个资源元数据，供 Retrieval 避免逐 Chunk 查询。"""

        unique_ids = tuple(dict.fromkeys(asset_ids))
        if not unique_ids:
            return {}
        async with self._session_factory() as session:
            values = await session.scalars(
                select(DocumentAssetModel).where(DocumentAssetModel.id.in_(unique_ids))
            )
            return {model.id: self._document_asset(model) for model in values}

    async def list_document_assets(self, document_ids: Sequence[str]) -> list[DocumentAsset]:
        """按文档集合批量返回资源事实，用于摄取补偿和删除生命周期。"""

        unique_ids = tuple(dict.fromkeys(document_ids))
        if not unique_ids:
            return []
        async with self._session_factory() as session:
            values = await session.scalars(
                select(DocumentAssetModel)
                .where(DocumentAssetModel.document_id.in_(unique_ids))
                .order_by(DocumentAssetModel.document_id, DocumentAssetModel.created_at)
            )
            return [self._document_asset(model) for model in values]

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

    async def list_ready_chunks(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> list[Chunk]:
        """批量返回指定 READY 文档的全部 Chunk，并保持文档、原文顺序。

        全文总结需要章节覆盖，不能先用向量相似度截成 Top-K。这里从 PostgreSQL 事实表一次性
        读取候选，既不会依赖可能过期的 Milvus 派生索引，也避免按文档逐个查询形成 N+1。
        调用方必须先得到 READY 文档交集；本方法仍重复校验状态，防止两次读取间状态变化。
        """

        unique_document_ids = tuple(dict.fromkeys(document_ids))
        if not unique_document_ids:
            return []
        async with self._session_factory() as session:
            rows = list(
                await session.execute(
                    select(ChunkModel, DocumentModel.knowledge_base_id, DocumentModel.filename)
                    .join(DocumentModel, DocumentModel.id == ChunkModel.document_id)
                    .where(
                        DocumentModel.knowledge_base_id == knowledge_base_id,
                        DocumentModel.status == DocumentStatus.READY.value,
                        DocumentModel.id.in_(unique_document_ids),
                    )
                    .order_by(DocumentModel.created_at, ChunkModel.chunk_index)
                )
            )
        return [
            self._chunk(model, row_knowledge_base_id, filename)
            for model, row_knowledge_base_id, filename in rows
        ]

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

    async def replace_document_assets(
        self,
        document_id: str,
        assets: Sequence[DocumentAsset],
    ) -> None:
        """在单事务中替换一份文档的资源元数据，保持 Worker 重试幂等。

        MinIO 对象会在应用层先以稳定 Key 写入。本事务只在所有新行可插入时整体提交；失败
        不会让 READY 文档看到半套资源事实，文档状态也仍由处理管线控制。
        """

        if any(asset.document_id != document_id for asset in assets):
            raise ValueError("all assets must belong to the replaced document")
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(DocumentAssetModel).where(DocumentAssetModel.document_id == document_id)
            )
            session.add_all(
                [
                    DocumentAssetModel(
                        id=asset.id,
                        document_id=asset.document_id,
                        block_id=asset.block_id,
                        kind=asset.kind.value,
                        object_key=asset.object_key,
                        media_type=asset.media_type,
                        filename=asset.filename,
                        title=asset.title,
                        description=asset.description,
                        sha256=asset.sha256,
                        locator=asset.locator.to_metadata() if asset.locator else {},
                    )
                    for asset in assets
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
    def _document_asset(model: DocumentAssetModel) -> DocumentAsset:
        """把资源 ORM 元数据恢复为领域事实；二进制仍由 MinIO 按 Key 读取。"""

        locator = SourceLocator.from_metadata(model.locator) if model.locator else None
        return DocumentAsset(
            id=model.id,
            document_id=model.document_id,
            block_id=model.block_id,
            kind=BlockType(model.kind),
            object_key=model.object_key,
            media_type=model.media_type,
            filename=model.filename,
            title=model.title,
            description=model.description,
            sha256=model.sha256,
            locator=locator,
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
    def _chat_session(model: ChatSessionModel) -> ChatSession:
        """把 ORM 会话恢复为不依赖 SQLAlchemy 的领域快照。"""

        return ChatSession(
            id=model.id,
            knowledge_base_id=model.knowledge_base_id,
            title=model.title,
            memory_summary=model.memory_summary,
            memory_through_sequence=model.memory_through_sequence,
            created_at=Repository._datetime(model.created_at),
            updated_at=Repository._datetime(model.updated_at),
        )

    @staticmethod
    def _chat_message(model: ChatMessageModel) -> ChatMessage:
        """把消息角色、状态与可选检索快照恢复为受控领域对象。"""

        return ChatMessage(
            id=model.id,
            session_id=model.session_id,
            sequence=model.sequence,
            role=ChatRole(model.role),
            status=ChatMessageStatus(model.status),
            content=model.content,
            error_message=model.error_message,
            created_at=Repository._datetime(model.created_at),
            updated_at=Repository._datetime(model.updated_at),
            evidence=Repository._chat_evidence(model.retrieval_evidence),
        )

    @staticmethod
    def _chat_evidence_metadata(evidence: ChatEvidence | None) -> dict[str, JsonValue] | None:
        """把问答证据转换为不包含二进制的 JSONB 审计快照。"""

        if evidence is None:
            return None

        def locator(value: SourceLocator | None) -> JsonValue:
            return value.to_metadata() if value else None

        def asset(value: DocumentAsset) -> dict[str, JsonValue]:
            return {
                "id": value.id,
                "document_id": value.document_id,
                "block_id": value.block_id,
                "kind": value.kind.value,
                "object_key": value.object_key,
                "media_type": value.media_type,
                "filename": value.filename,
                "title": value.title,
                "description": value.description,
                "sha256": value.sha256,
                "locator": locator(value.locator),
            }

        return {
            "citations": [
                {
                    "document_id": value.document_id,
                    "filename": value.filename,
                    "chunk_id": value.chunk_id,
                    "heading_path": list(value.heading_path),
                    "locator": locator(value.locator),
                    "context_chunk_ids": list(value.context_chunk_ids),
                }
                for value in evidence.citations
            ],
            "results": [
                {
                    "chunk_id": value.chunk_id,
                    "knowledge_base_id": value.knowledge_base_id,
                    "document_id": value.document_id,
                    "filename": value.filename,
                    "content": value.content,
                    "heading_path": list(value.heading_path),
                    "score": value.score,
                    "locator": locator(value.locator),
                    "dense_score": value.dense_score,
                    "sparse_score": value.sparse_score,
                    "fusion_score": value.fusion_score,
                    "rerank_score": value.rerank_score,
                    "retrieval_sources": list(value.retrieval_sources),
                    "matched_content": value.matched_content,
                    "context_chunk_ids": list(value.context_chunk_ids),
                    "content_types": [item.value for item in value.content_types],
                    "assets": [asset(item) for item in value.assets],
                }
                for value in evidence.results
            ],
            "trace": {
                "original_query": evidence.trace.original_query,
                "query_variants": list(evidence.trace.query_variants),
                "mode": evidence.trace.mode.value,
                "candidate_count": evidence.trace.candidate_count,
                "result_count": evidence.trace.result_count,
                "rewrite_applied": evidence.trace.rewrite_applied,
                "rerank_applied": evidence.trace.rerank_applied,
                "parent_expansion_applied": evidence.trace.parent_expansion_applied,
                "fallback_reasons": list(evidence.trace.fallback_reasons),
                "intent": evidence.trace.intent.value,
                "strategy": evidence.trace.strategy,
            },
        }

    @staticmethod
    def _chat_evidence(value: dict[str, object] | None) -> ChatEvidence | None:
        """从 JSONB 恢复历史证据；旧消息或不可用快照安全降级为纯文本。

        证据是答案的辅助快照，不能因旧版记录缺字段而让整个会话正文无法打开。这里严格
        构造领域枚举和必填字段；任一结构不合法就丢弃整份快照，消息内容仍照常返回。
        """

        if not value:
            return None
        try:
            raw_citations = cast(list[dict[str, object]], value["citations"])
            raw_results = cast(list[dict[str, object]], value["results"])
            raw_trace = cast(dict[str, object], value["trace"])

            def locator(raw: object) -> SourceLocator | None:
                return (
                    SourceLocator.from_metadata(cast(dict[str, object], raw))
                    if isinstance(raw, dict)
                    else None
                )

            citations = tuple(
                Citation(
                    document_id=str(item["document_id"]),
                    filename=str(item["filename"]),
                    chunk_id=str(item["chunk_id"]),
                    heading_path=tuple(
                        str(part) for part in Repository._object_list(item.get("heading_path"))
                    ),
                    locator=locator(item.get("locator")),
                    context_chunk_ids=tuple(
                        str(part) for part in Repository._object_list(item.get("context_chunk_ids"))
                    ),
                )
                for item in raw_citations
            )

            results: list[RetrievalResult] = []
            for item in raw_results:
                assets = tuple(
                    DocumentAsset(
                        id=str(asset["id"]),
                        document_id=str(asset["document_id"]),
                        block_id=str(asset["block_id"]),
                        kind=BlockType(str(asset["kind"])),
                        object_key=str(asset["object_key"]),
                        media_type=str(asset["media_type"]),
                        filename=str(asset["filename"]),
                        title=str(asset["title"]),
                        description=str(asset.get("description", "")),
                        sha256=str(asset["sha256"]),
                        locator=locator(asset.get("locator")),
                    )
                    for asset in cast(list[dict[str, object]], item.get("assets", []))
                )
                results.append(
                    RetrievalResult(
                        chunk_id=str(item["chunk_id"]),
                        knowledge_base_id=str(item["knowledge_base_id"]),
                        document_id=str(item["document_id"]),
                        filename=str(item["filename"]),
                        content=str(item["content"]),
                        heading_path=tuple(
                            str(part) for part in Repository._object_list(item.get("heading_path"))
                        ),
                        score=Repository._required_float(item["score"]),
                        locator=locator(item.get("locator")),
                        dense_score=Repository._optional_float(item.get("dense_score")),
                        sparse_score=Repository._optional_float(item.get("sparse_score")),
                        fusion_score=Repository._optional_float(item.get("fusion_score")),
                        rerank_score=Repository._optional_float(item.get("rerank_score")),
                        retrieval_sources=tuple(
                            str(part)
                            for part in Repository._object_list(item.get("retrieval_sources"))
                        ),
                        matched_content=(
                            str(item["matched_content"])
                            if item.get("matched_content") is not None
                            else None
                        ),
                        context_chunk_ids=tuple(
                            str(part)
                            for part in Repository._object_list(item.get("context_chunk_ids"))
                        ),
                        content_types=tuple(
                            BlockType(str(part))
                            for part in Repository._object_list(item.get("content_types"))
                        ),
                        assets=assets,
                    )
                )

            trace = RetrievalTrace(
                original_query=str(raw_trace["original_query"]),
                query_variants=tuple(
                    str(part) for part in cast(list[object], raw_trace["query_variants"])
                ),
                mode=RetrievalMode(str(raw_trace["mode"])),
                candidate_count=int(cast(int, raw_trace["candidate_count"])),
                result_count=int(cast(int, raw_trace["result_count"])),
                rewrite_applied=bool(raw_trace["rewrite_applied"]),
                rerank_applied=bool(raw_trace["rerank_applied"]),
                parent_expansion_applied=bool(raw_trace["parent_expansion_applied"]),
                fallback_reasons=tuple(
                    str(part) for part in cast(list[object], raw_trace["fallback_reasons"])
                ),
                intent=RetrievalIntent(str(raw_trace.get("intent", RetrievalIntent.FACT.value))),
                strategy=str(raw_trace.get("strategy", "ranked_retrieval")),
            )
            return ChatEvidence(citations=citations, results=tuple(results), trace=trace)
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        """把 JSON 可选数值恢复为浮点数。"""

        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _required_float(value: object) -> float:
        """恢复 JSON 必填数值，错误结构交给快照级降级处理。"""

        if not isinstance(value, (int, float)):
            raise TypeError("required evidence score must be numeric")
        return float(value)

    @staticmethod
    def _object_list(value: object) -> list[object]:
        """只接受 JSON Array，缺失可选列表时返回空列表。"""

        return value if isinstance(value, list) else []

    @staticmethod
    def _chat_title(question: str) -> str:
        """使用首问生成确定性标题，避免为展示名称额外调用模型。"""

        compact = " ".join(question.split())
        return compact if len(compact) <= 40 else f"{compact[:40]}…"

    @staticmethod
    def _datetime(value: datetime | None) -> datetime:
        """断言数据库默认时间已回填，避免构造不完整领域对象。"""
        if value is None:
            raise RuntimeError("Database did not populate timestamp")
        return value
