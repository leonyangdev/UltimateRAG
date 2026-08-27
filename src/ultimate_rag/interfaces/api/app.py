"""FastAPI 应用入口与基础设施装配。

外部客户端在 lifespan 中创建并复用；数据库表结构由 Alembic 管理，不在应用启动时隐式修改。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ultimate_rag.application import (
    ContextBuilder,
    DocumentLifecycleService,
    IngestionService,
    RAGService,
    RetrievalService,
)
from ultimate_rag.chunkers import StructureAwareMarkdownChunker
from ultimate_rag.config import get_settings
from ultimate_rag.domain.exceptions import (
    InvalidDocumentError,
    ResourceNotFoundError,
    UltimateRAGError,
)
from ultimate_rag.embeddings import BailianEmbedder
from ultimate_rag.generation import BailianLLMClient
from ultimate_rag.infrastructure.database import create_database
from ultimate_rag.infrastructure.storage import MinioObjectStorage
from ultimate_rag.interfaces.api.container import Container
from ultimate_rag.interfaces.api.routes import router
from ultimate_rag.parsers import MarkdownParser, ParserRegistry
from ultimate_rag.vectorstores import MilvusVectorStore

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """装配 V1 依赖、幂等初始化 Bucket/Collection，并在退出时释放数据库引擎。"""
    engine, repository = create_database(settings.database_url)
    storage = MinioObjectStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        settings.minio_secure,
    )
    embedder = BailianEmbedder(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        timeout=settings.model_timeout_seconds,
    )
    vector_store = MilvusVectorStore(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection=settings.milvus_collection,
        dimension=settings.embedding_dimension,
    )
    llm = BailianLLMClient(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.llm_model,
        timeout=settings.model_timeout_seconds,
    )
    registry = ParserRegistry([MarkdownParser()])
    chunker = StructureAwareMarkdownChunker(settings.chunk_max_chars, settings.chunk_overlap_chars)
    retrieval = RetrievalService(embedder, vector_store)
    app.state.container = Container(
        engine=engine,
        repository=repository,
        ingestion=IngestionService(
            repository=repository,
            storage=storage,
            parser_registry=registry,
            chunker=chunker,
            embedder=embedder,
            vector_store=vector_store,
            max_upload_bytes=settings.max_upload_bytes,
        ),
        retrieval=retrieval,
        rag=RAGService(retrieval, ContextBuilder(settings.context_max_chars), llm),
        lifecycle=DocumentLifecycleService(repository, storage, vector_store),
    )
    await storage.ensure_bucket()
    await vector_store.ensure_collection()
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(ResourceNotFoundError)
async def not_found_handler(_request: Request, exc: ResourceNotFoundError) -> JSONResponse:
    """把明确的资源缺失转换为稳定 404 响应。"""
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.exception_handler(InvalidDocumentError)
async def invalid_document_handler(_request: Request, exc: InvalidDocumentError) -> JSONResponse:
    """把文档输入错误转换为用户可修复的 400 响应。"""
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(UltimateRAGError)
async def application_error_handler(_request: Request, exc: UltimateRAGError) -> JSONResponse:
    """把已知处理故障转换为 502，且不向客户端暴露 Stack Trace。"""
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)})
