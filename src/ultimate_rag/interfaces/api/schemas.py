"""FastAPI 请求与响应模型。

API Schema 是外部数据验证边界，与内部领域 dataclass 分离，防止基础设施字段意外泄漏给客户端。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ultimate_rag.domain.models import Citation, Document, KnowledgeBase, RetrievalResult


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
    """限定知识库、查询文本和召回数量的检索请求。"""

    knowledge_base_id: str
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class RetrievalResultResponse(BaseModel):
    """包含可追溯来源与余弦分数的检索命中。"""

    chunk_id: str
    document_id: str
    filename: str
    content: str
    heading_path: list[str]
    score: float

    @classmethod
    def from_domain(cls, value: RetrievalResult) -> "RetrievalResultResponse":
        """把不可变领域结果转换为 JSON 友好结构。"""
        return cls(
            chunk_id=value.chunk_id,
            document_id=value.document_id,
            filename=value.filename,
            content=value.content,
            heading_path=list(value.heading_path),
            score=value.score,
        )


class ChatRequest(RetrievalRequest):
    """RAG 问答请求；外部字段使用更符合产品语义的 ``question``。"""

    query: str = Field(min_length=1, max_length=4000, alias="question")
    model_config = ConfigDict(populate_by_name=True)


class CitationResponse(BaseModel):
    """最终答案引用的文档、Chunk 与章节定位。"""

    document_id: str
    filename: str
    chunk_id: str
    heading_path: list[str]

    @classmethod
    def from_domain(cls, value: Citation) -> "CitationResponse":
        """把 Citation 领域对象映射为 API 响应。"""
        return cls(
            document_id=value.document_id,
            filename=value.filename,
            chunk_id=value.chunk_id,
            heading_path=list(value.heading_path),
        )


class ChatResponse(BaseModel):
    """答案、引用和基础检索调试信息的完整 V1 响应。"""

    answer: str
    citations: list[CitationResponse]
    retrieval_results: list[RetrievalResultResponse]


class ErrorResponse(BaseModel):
    """统一的可操作错误响应结构。"""

    detail: str
