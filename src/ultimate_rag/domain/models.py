"""不依赖框架的 UltimateRAG 领域模型。

这些对象用于应用层与各基础设施适配器之间传递业务语义，禁止绑定 FastAPI、SQLAlchemy、Milvus
或模型厂商 SDK。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


class DocumentStatus(StrEnum):
    """后台摄取管线的用户可观察状态；完整索引成功前绝不能进入 ``READY``。"""

    PENDING = "PENDING"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class IngestionJobStatus(StrEnum):
    """持久化摄取任务的内部调度状态。

    文档状态面向用户表达当前处理阶段；任务状态只负责 Worker 领取、重试与完成语义，
    二者刻意分开，避免把队列实现细节暴露到公开 API。
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class BlockType(StrEnum):
    """统一文档模型中可由不同 Parser 产生的语义块类型。"""

    HEADING = "HEADING"
    TEXT = "TEXT"
    CODE = "CODE"
    LIST = "LIST"
    QUOTE = "QUOTE"
    TABLE = "TABLE"
    IMAGE = "IMAGE"


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """跨文档格式的来源位置，供 Chunk、检索结果和 Citation 统一复用。

    字段均为可选，因为不同格式能提供的定位精度不同：Markdown/HTML 使用标题路径，PDF
    使用页码与可选边界框，Excel 使用 Sheet 与单元格范围，PowerPoint 使用幻灯片序号。
    """

    heading_path: tuple[str, ...] = ()
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    sheet: str | None = None
    cell_range: str | None = None
    slide: int | None = None

    def to_metadata(self) -> dict[str, JsonValue]:
        """转换为 PostgreSQL JSONB、Milvus JSON 和 API 都可接受的稳定字典。"""

        value: dict[str, JsonValue] = {"heading_path": list(self.heading_path)}
        if self.page is not None:
            value["page"] = self.page
        if self.bbox is not None:
            value["bbox"] = list(self.bbox)
        if self.sheet is not None:
            value["sheet"] = self.sheet
        if self.cell_range is not None:
            value["cell_range"] = self.cell_range
        if self.slide is not None:
            value["slide"] = self.slide
        return value

    @classmethod
    def from_metadata(cls, value: Mapping[str, object] | None) -> "SourceLocator":
        """兼容缺失字段并从持久化 JSON 恢复强类型定位信息。"""

        if not value:
            return cls()
        raw_bbox = value.get("bbox")
        bbox = None
        if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
            bbox = (
                float(raw_bbox[0]),
                float(raw_bbox[1]),
                float(raw_bbox[2]),
                float(raw_bbox[3]),
            )
        raw_heading = value.get("heading_path")
        heading_path = (
            tuple(str(item) for item in raw_heading) if isinstance(raw_heading, list) else ()
        )
        raw_page = value.get("page")
        page = int(raw_page) if isinstance(raw_page, (str, int, float)) else None
        raw_slide = value.get("slide")
        slide = int(raw_slide) if isinstance(raw_slide, (str, int, float)) else None
        return cls(
            heading_path=heading_path,
            page=page,
            bbox=bbox,
            sheet=str(value["sheet"]) if value.get("sheet") is not None else None,
            cell_range=(str(value["cell_range"]) if value.get("cell_range") is not None else None),
            slide=slide,
        )

    def display(self) -> str:
        """生成面向 Prompt 和前端的紧凑定位文本。"""

        parts: list[str] = []
        if self.heading_path:
            parts.append(" / ".join(self.heading_path))
        if self.page is not None:
            parts.append(f"第 {self.page} 页")
        if self.sheet:
            parts.append(f"工作表 {self.sheet}")
        if self.cell_range:
            parts.append(f"区域 {self.cell_range}")
        if self.slide is not None:
            parts.append(f"第 {self.slide} 张幻灯片")
        return " · ".join(parts) or "未提供原文定位"


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
    locator: SourceLocator | None = None
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
    locator: SourceLocator | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """面向 API 消费者的来源引用，不暴露向量库内部字段。"""

    document_id: str
    filename: str
    chunk_id: str
    heading_path: tuple[str, ...]
    locator: SourceLocator | None = None


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


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """Worker 已领取或可领取的持久化文档摄取任务快照。"""

    id: str
    document_id: str
    status: IngestionJobStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    locked_at: datetime | None
    worker_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
