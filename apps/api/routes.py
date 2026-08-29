"""UltimateRAG V2 HTTP 路由。

模块职责：
    验证 HTTP 输入、调用应用服务，并把领域结果映射为普通 JSON 或 AI SDK UI Message Stream。

架构边界：
    路由不包含数据库、对象存储、向量检索或 Prompt 业务逻辑。流式端点也只做传输编码，
    模型供应商的增量响应由 ``LLMClient`` 适配器处理。

流式协议：
    ``/api/chat/stream`` 使用 AI SDK Data Stream Protocol 的 SSE 表示。除答案文本外，
    Citation 与 RetrievalResult 通过有类型的 ``data-retrieval`` Part 同消息返回，
    前端不需要在流结束后再发一次请求补取证据。

注意事项：
    检索在 StreamingResponse 建立前完成，因此资源缺失与检索异常仍能返回结构化 HTTP 状态；
    LLM 在响应开始后的故障只能编码为 ``error`` Part，不能再修改已经发送的状态码。
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from api.container import Container
from api.schemas import (
    ChatRequest,
    ChatResponse,
    CitationResponse,
    DocumentResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    RetrievalRequest,
    RetrievalResultResponse,
)
from ultimate_rag.domain.exceptions import InvalidDocumentError

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _ui_stream_event(payload: dict[str, object]) -> str:
    """把一个 AI SDK UI Message Chunk 编码为独立 SSE 事件。

    ``ensure_ascii=False`` 保留中文可读性；紧凑分隔符减少高频 text-delta 的传输开销。
    每个事件必须以两个换行结束，否则浏览器不会认为这个 SSE 事件已经完成。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def container(request: Request) -> Container:
    """从 FastAPI 应用状态读取 lifespan 已完成装配的依赖容器。"""
    value: Container = request.app.state.container
    return value


async def _read_bounded_upload(file: UploadFile, max_upload_bytes: int) -> bytes:
    """最多读取上传上限加一个字节，让超限请求在 HTTP 边界尽早失败。

    应用服务仍保留同样的大小校验，因为它也可能被 CLI、测试或未来 Worker 直接调用；
    HTTP 层的有界读取属于防御性保护，避免先把任意大的请求完整加载到进程内存后才拒绝。

    Args:
        file: FastAPI 已放入临时缓冲区的上传文件。
        max_upload_bytes: 当前部署允许交给应用服务的最大字节数。

    Returns:
        不超过限制的完整文件内容。

    Raises:
        InvalidDocumentError: 读取到限制之外的额外字节时抛出。
    """

    content = await file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise InvalidDocumentError(f"文件不能超过 {max_upload_bytes // (1024 * 1024)} MB")
    return content


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
    """上传并同步完成多格式文档的解析、切块、向量化和索引。"""
    dependencies = container(request)
    content = await _read_bounded_upload(file, dependencies.max_upload_bytes)
    value = await dependencies.ingestion.ingest(
        knowledge_base_id,
        file.filename or "document.bin",
        file.content_type or "application/octet-stream",
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


@router.post("/chat/stream")
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """以 AI SDK UI Message Stream 执行可追溯的流式 RAG 问答。

    检索和 Citation 构造先于响应开始，这使知识库不存在、Embedding 失败或 Milvus 不可用时
    仍能走统一 FastAPI 异常映射。模型增量随后直接透传为 ``text-delta``，没有前端假流式。

    Args:
        payload: 知识库范围、自然语言问题与召回上限。
        request: FastAPI 请求，用于读取 Lifespan 已装配的应用服务。

    Returns:
        ``text/event-stream`` 响应。首个数据 Part 携带 Citation 与召回证据，随后发送答案增量。
    """

    await container(request).repository.get_knowledge_base(payload.knowledge_base_id)
    answer_stream, citations, results = await container(request).rag.stream_answer(
        payload.knowledge_base_id, payload.query, payload.top_k
    )

    # Evidence 在模型生成前已经稳定。通过自定义 data Part 与同一 assistant message 绑定，
    # 前端即使在答案仍生成时也能展示“依据了哪些 Chunk”，且无需维护第二套请求状态。
    retrieval_data = {
        "citations": [
            CitationResponse.from_domain(value).model_dump(mode="json") for value in citations
        ],
        "retrieval_results": [
            RetrievalResultResponse.from_domain(value).model_dump(mode="json") for value in results
        ],
    }
    message_id = f"msg-{uuid4()}"
    text_id = f"text-{uuid4()}"

    async def event_stream() -> AsyncIterator[str]:
        """按 AI SDK 要求维护 start/text/finish 事件顺序。"""

        yield _ui_stream_event({"type": "start", "messageId": message_id})
        yield _ui_stream_event({"type": "start-step"})
        yield _ui_stream_event({"type": "data-retrieval", "data": retrieval_data})
        yield _ui_stream_event({"type": "text-start", "id": text_id})

        try:
            async for delta in answer_stream:
                yield _ui_stream_event({"type": "text-delta", "id": text_id, "delta": delta})
        except Exception:
            # 此时 200 状态和部分内容可能已经送达，无法再交给普通异常 Handler。
            # 日志保留完整堆栈用于排查，浏览器只收到稳定文案，避免泄漏供应商响应或凭据。
            logger.exception(
                "Streaming RAG generation failed",
                extra={"knowledge_base_id": payload.knowledge_base_id},
            )
            yield _ui_stream_event({"type": "text-end", "id": text_id})
            yield _ui_stream_event({"type": "error", "errorText": "生成过程中断，请稍后重试。"})
            yield "data: [DONE]\n\n"
            return

        yield _ui_stream_event({"type": "text-end", "id": text_id})
        yield _ui_stream_event({"type": "finish-step"})
        yield _ui_stream_event({"type": "finish", "finishReason": "stop"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx 默认可能缓冲小响应；关闭代理缓冲才能让 token 及时抵达浏览器。
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )
