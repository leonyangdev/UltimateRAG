"""不依赖框架的 UltimateRAG 领域模型。

这些对象用于应用层与各基础设施适配器之间传递业务语义，禁止绑定 FastAPI、SQLAlchemy、Milvus
或模型厂商 SDK。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


class DocumentStatus(StrEnum):
    """同步摄取管线的可观察状态；只有完整索引成功后才能进入 ``READY``。"""

    PENDING = "PENDING"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class BlockType(StrEnum):
    """统一文档模型中 V1 已识别的语义块类型。"""

    HEADING = "HEADING"
    TEXT = "TEXT"
    CODE = "CODE"
    LIST = "LIST"
    QUOTE = "QUOTE"


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """内容在原文中的可追溯位置；V1 使用 Markdown 标题路径定位。"""

    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentSource:
    """交给解析器的原始文档快照，不包含任何基础设施句柄。"""

    document_id: str
    filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class Block:
    """解析器输出的最小语义结构单元。"""

    id: str
    type: BlockType
    content: str
    locator: SourceLocator | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """统一解析结果，使后续切块逻辑无需感知原始文件格式。"""

    document_id: str
    blocks: tuple[Block, ...]
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    """可独立向量化、检索并引用的稳定文本单元。"""

    id: str
    knowledge_base_id: str
    document_id: str
    index: int
    content: str
    heading_path: tuple[str, ...]
    token_count: int
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """写入向量库前的 Chunk 与稠密向量组合。"""

    chunk: Chunk
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """向量检索命中结果，保留答案引用所需的完整来源信息。"""

    chunk_id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    content: str
    heading_path: tuple[str, ...]
    score: float


@dataclass(frozen=True, slots=True)
class Citation:
    """面向 API 消费者的来源引用，不暴露向量库内部字段。"""

    document_id: str
    filename: str
    chunk_id: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    """知识库领域对象，是文档和检索范围的顶层容器。"""

    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Document:
    """原始文档的业务元数据及其当前处理状态。"""

    id: str
    knowledge_base_id: str
    filename: str
    mime_type: str
    extension: str
    object_key: str
    sha256: str
    status: DocumentStatus
    parser_name: str | None
    parser_version: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
