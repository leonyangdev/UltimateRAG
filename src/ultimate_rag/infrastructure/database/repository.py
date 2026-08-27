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
            document_models = list(
                await session.scalars(
                    select(DocumentModel).where(
                        DocumentModel.knowledge_base_id == knowledge_base_id
                    )
                )
            )
            documents = [self._document(document) for document in document_models]
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
            if await session.get(KnowledgeBaseModel, knowledge_base_id) is None:
                raise ResourceNotFoundError("知识库不存在")
            session.add(model)
        return self._document(model)

    async def list_documents(self, knowledge_base_id: str) -> list[Document]:
        """返回知识库文档；知识库不存在与空知识库使用不同语义。"""
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
            model.status = status.value
            model.error_message = error_message
            if parser_name is not None:
                model.parser_name = parser_name
            if parser_version is not None:
                model.parser_version = parser_version

    async def replace_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """在事务中替换文档 Chunk，保证重试不会产生重复事实记录。"""
        async with self._session_factory() as session, session.begin():
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
