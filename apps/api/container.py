"""进程级依赖容器。

容器在 FastAPI lifespan 中显式装配，避免路由内部创建外部客户端或使用隐式全局可变状态。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ultimate_rag.application import (
    DocumentLifecycleService,
    IngestionService,
    RAGService,
    RetrievalService,
)
from ultimate_rag.domain.models import RetrievalOptions
from ultimate_rag.infrastructure.database.repository import Repository


@dataclass(slots=True)
class Container:
    """持有 API 生命周期内共享的引擎、Repository 和应用服务。"""

    engine: AsyncEngine
    max_upload_bytes: int
    repository: Repository
    ingestion: IngestionService
    retrieval: RetrievalService
    retrieval_defaults: RetrievalOptions
    rag: RAGService
    lifecycle: DocumentLifecycleService
