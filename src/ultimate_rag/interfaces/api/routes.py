"""UltimateRAG V1 HTTP 路由。

路由只负责输入验证、调用应用服务和响应映射，不包含数据库、对象存储或 Prompt 业务逻辑。
"""

from typing import Annotated

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from ultimate_rag.interfaces.api.container import Container
from ultimate_rag.interfaces.api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationResponse,
    DocumentResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    RetrievalRequest,
    RetrievalResultResponse,
)

router = APIRouter(prefix="/api")


def container(request: Request) -> Container:
    """从 FastAPI 应用状态读取 lifespan 已完成装配的依赖容器。"""
    value: Container = request.app.state.container
    return value


@router.get("/health")
async def health() -> dict[str, str]:
    """返回进程存活状态；不触发昂贵的外部模型调用。"""
    return {"status": "ok"}


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, request: Request
) -> KnowledgeBaseResponse:
    """创建一个可独立管理文档和检索范围的知识库。"""
    value = await container(request).repository.create_knowledge_base(
        payload.name.strip(), payload.description.strip()
    )
    return KnowledgeBaseResponse.from_domain(value)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(request: Request) -> list[KnowledgeBaseResponse]:
    """列出全部知识库。"""
    values = await container(request).repository.list_knowledge_bases()
    return [KnowledgeBaseResponse.from_domain(value) for value in values]


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(knowledge_base_id: str, request: Request) -> KnowledgeBaseResponse:
    """读取单个知识库。"""
    value = await container(request).repository.get_knowledge_base(knowledge_base_id)
    return KnowledgeBaseResponse.from_domain(value)


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(knowledge_base_id: str, request: Request) -> Response:
    """同步删除知识库及其跨存储资源。"""
    await container(request).lifecycle.delete_knowledge_base(knowledge_base_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    knowledge_base_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    """上传并同步完成 Markdown 的解析、切块、向量化和索引。"""
    content = await file.read()
    value = await container(request).ingestion.ingest(
        knowledge_base_id,
        file.filename or "document.md",
        file.content_type or "text/markdown",
        content,
    )
    return DocumentResponse.from_domain(value)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(knowledge_base_id: str, request: Request) -> list[DocumentResponse]:
    """列出知识库中的文档和实时处理状态。"""
    values = await container(request).repository.list_documents(knowledge_base_id)
    return [DocumentResponse.from_domain(value) for value in values]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, request: Request) -> DocumentResponse:
    """读取一份文档的处理元数据。"""
    value = await container(request).repository.get_document(document_id)
    return DocumentResponse.from_domain(value)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, request: Request) -> Response:
    """同步删除文档原文件、Chunk 和派生向量。"""
    await container(request).lifecycle.delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/retrieval/search", response_model=list[RetrievalResultResponse])
async def search(payload: RetrievalRequest, request: Request) -> list[RetrievalResultResponse]:
    """执行不依赖 LLM 的 Dense Retrieval，供效果调试和独立测试。"""
    await container(request).repository.get_knowledge_base(payload.knowledge_base_id)
    results = await container(request).retrieval.search(
        payload.knowledge_base_id, payload.query, payload.top_k
    )
    return [RetrievalResultResponse.from_domain(result) for result in results]


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """执行完整 RAG 问答并返回答案、引用与召回证据。"""
    await container(request).repository.get_knowledge_base(payload.knowledge_base_id)
    answer, citations, results = await container(request).rag.answer(
        payload.knowledge_base_id, payload.query, payload.top_k
    )
    return ChatResponse(
        answer=answer,
        citations=[CitationResponse.from_domain(value) for value in citations],
        retrieval_results=[RetrievalResultResponse.from_domain(value) for value in results],
    )
