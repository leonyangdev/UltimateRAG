"""FastAPI 应用入口与进程级依赖装配。

模块职责：
    创建 FastAPI 应用，在 Lifespan 中装配数据库、对象存储、向量库、模型客户端与
    应用服务，并注册中间件、路由和统一异常映射。

架构边界：
    本模块位于 Interface 层，只负责进程启动与 HTTP 边界。文档摄取、检索和生成的
    业务顺序由 Application Service 编排，外部协议细节由 Infrastructure Adapter 负责。

设计背景：
    V3 使用一个显式 Container 保存进程内共享依赖。相比在每个 Route 中临时创建客户端，
    这种方式可以复用连接池并让对象生命周期可见；当前依赖数量有限，因此不引入 DI 框架。

典型使用场景：
    Uvicorn 导入本模块中的 ``app``，随后由 FastAPI 调用 ``lifespan()`` 完成启动和关闭。

注意事项 / 已知限制：
    数据库 Schema 只能通过 Alembic Migration 管理，启动过程不会自动建表。MinIO Bucket
    或 Milvus Collection 初始化失败时应用不会进入请求服务阶段。V3 关闭时显式释放数据库
    Engine；其他 Adapter 当前没有统一的异步关闭端口。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.container import Container
from api.routes import router
from ultimate_rag.application import (
    ChatService,
    ContextBuilder,
    ConversationMemoryService,
    DocumentLifecycleService,
    RAGService,
    RetrievalService,
    VisualEvidenceService,
)
from ultimate_rag.config import get_settings
from ultimate_rag.domain.exceptions import (
    ChatSessionBusyError,
    DocumentBusyError,
    InvalidDocumentError,
    ResourceNotFoundError,
    UltimateRAGError,
)
from ultimate_rag.domain.models import RetrievalMode, RetrievalOptions
from ultimate_rag.generation import BailianLLMClient
from ultimate_rag.infrastructure.pdf_preview import PDFiumPreviewRenderer
from ultimate_rag.retrieval import BailianQueryRewriter, BailianReranker
from ultimate_rag.runtime import create_processing_runtime

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """在 FastAPI 进程生命周期内创建、校验并暴露 V3 所需依赖。

    Lifespan 的启动部分严格先于 ``yield`` 执行。只有 MinIO Bucket 和 Milvus Collection
    均准备完成后，FastAPI 才开始接收请求；因此 Route 不需要处理“依赖尚未初始化”的状态。

    Args:
        app: 当前 FastAPI 应用，用于通过 ``app.state`` 暴露进程级依赖容器。

    Yields:
        无业务值；执行到 ``yield`` 表示启动检查完成，可以开始处理 HTTP 请求。

    Raises:
        Exception: 数据库适配器、MinIO 或 Milvus 初始化失败时保留原始异常并中止启动。

    Side Effects:
        创建外部服务客户端，幂等创建 Bucket/Collection，写入 ``app.state.container``，
        并在正常关闭阶段释放 SQLAlchemy Engine 的连接池。
    """

    # API 与 Worker 从同一 Composition Root 装配 Parser、Chunker 和事实存储，防止上传校验
    # 支持某格式、后台进程却没有对应 Parser。API 只使用其中轻量的提交服务，不执行解析。
    runtime = create_processing_runtime(settings)

    # LLM 只属于 HTTP 问答进程；后台 Worker 不需要创建生成模型客户端。
    llm = BailianLLMClient(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.llm_model,
        timeout=settings.model_timeout_seconds,
    )
    query_rewriter = BailianQueryRewriter(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.query_rewrite_model,
        timeout=settings.model_timeout_seconds,
    )
    reranker = BailianReranker(
        api_key=settings.dashscope_api_key,
        url=settings.rerank_url,
        model=settings.rerank_model,
        timeout=settings.model_timeout_seconds,
        max_request_tokens=settings.rerank_max_request_tokens,
        # 复用摄取阶段的 tokenizer，使 Chunk 预算和 Rerank 本地估算采用同一基线。
        tokenizer_name=settings.chunk_tokenizer,
    )
    retrieval_defaults = RetrievalOptions(
        mode=RetrievalMode.HYBRID,
        candidate_k=settings.retrieval_candidate_k,
        enable_query_rewrite=settings.retrieval_query_rewrite,
        enable_rerank=settings.retrieval_rerank,
        enable_parent_expansion=settings.retrieval_parent_expansion,
    )
    retrieval = RetrievalService(
        runtime.embedder,
        runtime.vector_store,
        runtime.repository,
        query_rewriter=query_rewriter,
        reranker=reranker,
        default_options=retrieval_defaults,
        rrf_k=settings.retrieval_rrf_k,
        parent_window=settings.retrieval_parent_window,
        parent_max_tokens=settings.retrieval_parent_max_tokens,
        summary_max_chunks=settings.summary_max_chunks,
        summary_max_tokens=settings.summary_max_tokens,
    )

    rag = RAGService(
        retrieval,
        ContextBuilder(settings.context_max_chars),
        llm,
        summary_context_builder=ContextBuilder(settings.summary_context_max_chars),
    )
    memory = ConversationMemoryService(
        repository=runtime.repository,
        llm=llm,
        recent_token_budget=settings.chat_recent_token_budget,
        memory_max_tokens=settings.chat_memory_max_tokens,
        tokenizer_name=settings.chunk_tokenizer,
    )

    # 阶段 4：把已经装配好的对象集中放入进程级 Container。
    # Route 只从 app.state 取应用服务，不自行读取配置或创建客户端，从而保持 HTTP 层轻量，
    # 也确保摄取、检索和删除流程使用的是同一组 Repository、Storage 与 VectorStore 实例。
    app.state.container = Container(
        engine=runtime.engine,
        max_upload_bytes=settings.max_upload_bytes,
        repository=runtime.repository,
        ingestion=runtime.ingestion,
        retrieval=retrieval,
        retrieval_defaults=retrieval_defaults,
        rag=rag,
        chat=ChatService(
            repository=runtime.repository,
            rag=rag,
            memory=memory,
            stale_after_seconds=settings.chat_generation_stale_seconds,
        ),
        visual_evidence=VisualEvidenceService(
            repository=runtime.repository,
            storage=runtime.storage,
            renderer=PDFiumPreviewRenderer(),
        ),
        lifecycle=DocumentLifecycleService(
            runtime.repository,
            runtime.storage,
            runtime.vector_store,
        ),
    )

    # 阶段 5：在开放 HTTP 服务之前完成外部资源的幂等准备。
    # Bucket/Collection 不存在时创建，存在时复用；任一步抛出异常都会阻止执行 yield，
    # FastAPI 因而不会在依赖不可用或向量 Schema 未准备好时对外宣称启动成功。
    try:
        await runtime.initialize()

        # 生命周期分界点：yield 之前属于启动阶段，yield 期间由 FastAPI 处理请求。
        yield
    finally:
        # 初始化中途失败也必须释放已经创建的数据库连接池。
        await runtime.close()


app = FastAPI(title=settings.app_name, version="3.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # AI SDK 用该响应头识别 UI Message Stream；跨域开发时浏览器只有在此显式暴露后
    # 才允许前端读取，避免把合法 SSE 响应误判为普通文本流。
    expose_headers=["x-vercel-ai-ui-message-stream"],
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


@app.exception_handler(DocumentBusyError)
async def document_busy_handler(_request: Request, exc: DocumentBusyError) -> JSONResponse:
    """处理中的文档存在并发写入风险，使用 409 提示客户端稍后重试。"""

    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(ChatSessionBusyError)
async def chat_session_busy_handler(_request: Request, exc: ChatSessionBusyError) -> JSONResponse:
    """同一会话串行生成，避免并发请求交叉写入上下文顺序。"""

    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(UltimateRAGError)
async def application_error_handler(_request: Request, exc: UltimateRAGError) -> JSONResponse:
    """把已知处理故障转换为 502，且不向客户端暴露 Stack Trace。"""
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)})
