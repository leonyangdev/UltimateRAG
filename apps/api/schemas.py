"""FastAPI 请求与响应模型。

API Schema 是外部数据验证边界，与内部领域 dataclass 分离，防止基础设施字段意外泄漏给客户端。
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ultimate_rag.domain.models import (
    ChatMessage,
    ChatSession,
    Citation,
    Document,
    KnowledgeBase,
    RetrievalIntent,
    RetrievalMode,
    RetrievalOptions,
    RetrievalResult,
    RetrievalTrace,
    SourceLocator,
)


class SourceLocatorResponse(BaseModel):
    """跨格式原文位置；不同文档类型只填写适用字段。"""

    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None
    bbox: list[float] | None = None
    sheet: str | None = None
    cell_range: str | None = None
    slide: int | None = None

    @classmethod
    def from_domain(cls, value: SourceLocator | None) -> "SourceLocatorResponse | None":
        """把不可变 Locator 映射为 JSON 友好的 API 结构。"""

        if value is None:
            return None
        return cls(
            heading_path=list(value.heading_path),
            page=value.page,
            bbox=list(value.bbox) if value.bbox else None,
            sheet=value.sheet,
            cell_range=value.cell_range,
            slide=value.slide,
        )


class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求。"""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class KnowledgeBaseResponse(BaseModel):
    """知识库公开字段响应。"""

    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: KnowledgeBase) -> "KnowledgeBaseResponse":
        """显式映射知识库领域对象，保持 API 字段稳定。"""
        return (
            cls(**value.__dict__)
            if hasattr(value, "__dict__")
            else cls(
                id=value.id,
                name=value.name,
                description=value.description,
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )


class DocumentResponse(BaseModel):
    """文档元数据和用户可操作的处理状态。"""

    id: str
    knowledge_base_id: str
    filename: str
    mime_type: str
    extension: str
    sha256: str
    status: str
    parser_name: str | None
    parser_version: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: Document) -> "DocumentResponse":
        """映射文档领域对象，并将枚举状态序列化为字符串。"""
        return cls(
            id=value.id,
            knowledge_base_id=value.knowledge_base_id,
            filename=value.filename,
            mime_type=value.mime_type,
            extension=value.extension,
            sha256=value.sha256,
            status=value.status.value,
            parser_name=value.parser_name,
            parser_version=value.parser_version,
            error_message=value.error_message,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class RetrievalRequest(BaseModel):
    """限定业务范围，并允许逐请求覆盖 V3 检索策略。"""

    knowledge_base_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: RetrievalMode | None = None
    candidate_k: int | None = Field(default=None, ge=1, le=100)
    enable_query_rewrite: bool | None = None
    enable_rerank: bool | None = None
    enable_parent_expansion: bool | None = None
    document_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list,
        max_length=50,
    )

    @field_validator("knowledge_base_id", "query")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """拒绝只含空白的业务标识和查询，并移除无语义的首尾空白。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, values: list[str]) -> list[str]:
        """在 HTTP 边界清理文档白名单，避免空 ID 到应用层才触发 500。"""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("document_ids cannot contain blank values")
        return normalized

    def to_options(self, defaults: RetrievalOptions) -> RetrievalOptions:
        """把可选 API 覆盖项与部署默认值合并为不可变领域配置。"""

        # dict.fromkeys 在保留用户顺序的同时去重，避免相同 ID 放大 Milvus 过滤表达式。
        document_ids = tuple(dict.fromkeys(self.document_ids))
        return RetrievalOptions(
            mode=self.mode or defaults.mode,
            candidate_k=self.candidate_k or defaults.candidate_k,
            enable_query_rewrite=(
                defaults.enable_query_rewrite
                if self.enable_query_rewrite is None
                else self.enable_query_rewrite
            ),
            enable_rerank=(
                defaults.enable_rerank if self.enable_rerank is None else self.enable_rerank
            ),
            enable_parent_expansion=(
                defaults.enable_parent_expansion
                if self.enable_parent_expansion is None
                else self.enable_parent_expansion
            ),
            document_ids=document_ids,
        )


class RetrievalResultResponse(BaseModel):
    """包含来源、最终分数和各检索阶段解释字段的命中。"""

    chunk_id: str
    document_id: str
    filename: str
    content: str
    heading_path: list[str]
    locator: SourceLocatorResponse | None
    score: float
    dense_score: float | None
    sparse_score: float | None
    fusion_score: float | None
    rerank_score: float | None
    retrieval_sources: list[str]
    matched_content: str | None
    context_chunk_ids: list[str]
    content_types: list[str]
    # 相对路径允许前端沿用当前 API Origin；只有带 PDF 页码的命中才提供预览入口。
    preview_url: str | None

    @classmethod
    def from_domain(cls, value: RetrievalResult) -> "RetrievalResultResponse":
        """把不可变领域结果转换为 JSON 友好结构。"""
        return cls(
            chunk_id=value.chunk_id,
            document_id=value.document_id,
            filename=value.filename,
            content=value.content,
            heading_path=list(value.heading_path),
            locator=SourceLocatorResponse.from_domain(value.locator),
            score=value.score,
            dense_score=value.dense_score,
            sparse_score=value.sparse_score,
            fusion_score=value.fusion_score,
            rerank_score=value.rerank_score,
            retrieval_sources=list(value.retrieval_sources),
            matched_content=value.matched_content,
            context_chunk_ids=list(value.context_chunk_ids),
            content_types=[item.value for item in value.content_types],
            preview_url=(
                f"/api/chunks/{value.chunk_id}/preview"
                if value.locator is not None and value.locator.page is not None
                else None
            ),
        )


class RetrievalTraceResponse(BaseModel):
    """面向 Retrieval Playground 的 V3 阶段说明。"""

    original_query: str
    query_variants: list[str]
    mode: RetrievalMode
    candidate_count: int
    result_count: int
    rewrite_applied: bool
    rerank_applied: bool
    parent_expansion_applied: bool
    fallback_reasons: list[str]
    intent: RetrievalIntent
    strategy: str

    @classmethod
    def from_domain(cls, value: RetrievalTrace) -> "RetrievalTraceResponse":
        """把不可变 Trace 转换为 JSON 列表结构。"""

        return cls(
            original_query=value.original_query,
            query_variants=list(value.query_variants),
            mode=value.mode,
            candidate_count=value.candidate_count,
            result_count=value.result_count,
            rewrite_applied=value.rewrite_applied,
            rerank_applied=value.rerank_applied,
            parent_expansion_applied=value.parent_expansion_applied,
            fallback_reasons=list(value.fallback_reasons),
            intent=value.intent,
            strategy=value.strategy,
        )


class RetrievalExplainResponse(BaseModel):
    """V3 调试端点的结果信封；旧 ``/search`` 仍返回兼容数组。"""

    results: list[RetrievalResultResponse]
    trace: RetrievalTraceResponse


class ChatRequest(RetrievalRequest):
    """RAG 问答请求；外部字段使用更符合产品语义的 ``question``。"""

    query: str = Field(min_length=1, max_length=4000, alias="question")
    # 保持旧客户端无 session_id 时的无状态行为；新版页面始终显式传入持久化会话。
    session_id: str | None = Field(default=None, min_length=36, max_length=36)
    model_config = ConfigDict(populate_by_name=True)


class ChatSessionResponse(BaseModel):
    """知识库历史会话列表项。"""

    id: str
    knowledge_base_id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: ChatSession) -> "ChatSessionResponse":
        return cls(
            id=value.id,
            knowledge_base_id=value.knowledge_base_id,
            title=value.title,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class ChatMessageResponse(BaseModel):
    """恢复历史会话所需的稳定消息字段。"""

    id: str
    role: str
    status: str
    content: str
    error_message: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, value: ChatMessage) -> "ChatMessageResponse":
        return cls(
            id=value.id,
            role=value.role.value,
            status=value.status.value,
            content=value.content,
            error_message=value.error_message,
            created_at=value.created_at,
        )


class ChatSessionDetailResponse(BaseModel):
    """会话元数据及按序消息，用于刷新或选择历史会话。"""

    session: ChatSessionResponse
    messages: list[ChatMessageResponse]


class CitationResponse(BaseModel):
    """最终答案引用的文档、Chunk 与章节定位。"""

    document_id: str
    filename: str
    chunk_id: str
    heading_path: list[str]
    locator: SourceLocatorResponse | None
    context_chunk_ids: list[str]

    @classmethod
    def from_domain(cls, value: Citation) -> "CitationResponse":
        """把 Citation 领域对象映射为 API 响应。"""
        return cls(
            document_id=value.document_id,
            filename=value.filename,
            chunk_id=value.chunk_id,
            heading_path=list(value.heading_path),
            locator=SourceLocatorResponse.from_domain(value.locator),
            context_chunk_ids=list(value.context_chunk_ids),
        )


class ChatResponse(BaseModel):
    """答案、跨格式引用和高级检索解释信息的完整 V3 响应。"""

    answer: str
    citations: list[CitationResponse]
    retrieval_results: list[RetrievalResultResponse]
    retrieval_trace: RetrievalTraceResponse


class ErrorResponse(BaseModel):
    """统一的可操作错误响应结构。"""

    detail: str
