"""面向业务语义的 PostgreSQL Repository。

Repository 负责事务边界和 ORM/领域模型映射，不编排 MinIO、Milvus 或模型服务调用。
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ultimate_rag.domain.exceptions import ResourceNotFoundError
from ultimate_rag.domain.models import Chunk, Document, DocumentStatus, KnowledgeBase
from ultimate_rag.infrastructure.database.models import (
    ChunkModel,
    DocumentModel,
    KnowledgeBaseModel,
)


class Repository:
    """封装 V1 知识库、文档和 Chunk 的数据库读写。"""

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
    def _datetime(value: datetime | None) -> datetime:
        """断言数据库默认时间已回填，避免构造不完整领域对象。"""
        if value is None:
            raise RuntimeError("Database did not populate timestamp")
        return value
