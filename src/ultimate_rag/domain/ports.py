"""核心可替换能力的最小端口协议。

协议由应用层依赖、外围适配器实现；这里只描述当前 V3 真实需要的行为，不预设未来插件运行时。
"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from ultimate_rag.domain.models import (
    Chunk,
    DocumentSource,
    EmbeddedChunk,
    ParsedDocument,
    RerankResult,
    RetrievalResult,
)


class DocumentParser(Protocol):
    """把一种受支持的原始文档转换为统一文档模型。"""

    name: str
    version: str

    def supports(self, source: DocumentSource) -> bool:
        """判断当前解析器是否支持该文档来源。"""
        ...

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """解析原始内容，失败时抛出具有业务含义的文档异常。"""
        ...


class Chunker(Protocol):
    """把统一文档模型切分为可追踪且可向量化的 Chunk。"""

    async def split(self, document: ParsedDocument, knowledge_base_id: str) -> list[Chunk]:
        """切分文档并为每个结果生成稳定 ID。"""
        ...


class Embedder(Protocol):
    """稠密文本向量服务边界，隔离具体模型供应商。"""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化文档文本，并保持输入输出顺序一致。"""
        ...

    async def embed_query(self, query: str) -> list[float]:
        """使用与文档相同的向量空间编码查询。"""
        ...


class VectorStore(Protocol):
    """派生向量索引边界；业务事实不得只保存在此处。"""

    async def ensure_collection(self) -> None:
        """幂等创建当前版本所需的向量集合和索引。"""
        ...

    async def upsert(self, chunks: Sequence[EmbeddedChunk]) -> None:
        """按稳定 Chunk ID 幂等写入 Dense 与 Sparse 派生索引。"""
        ...

    async def upsert_sparse(self, chunks: Sequence[Chunk]) -> None:
        """只重建 BM25 索引，不重复调用计费 Embedding 服务。"""
        ...

    async def search(
        self,
        query_vector: Sequence[float],
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
    ) -> list[RetrievalResult]:
        """在知识库与可选文档过滤范围内执行 Dense 相似度检索。"""
        ...

    async def search_sparse(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
    ) -> list[RetrievalResult]:
        """在相同业务范围内执行由原始文本驱动的 BM25 检索。"""
        ...

    async def delete_by_document(self, document_id: str) -> None:
        """删除指定文档的全部派生向量。"""
        ...

    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None:
        """删除指定知识库的全部派生向量。"""
        ...


class ObjectStorage(Protocol):
    """原始文件对象存储边界。"""

    async def ensure_bucket(self) -> None:
        """幂等保证文档 Bucket 存在。"""
        ...

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        """使用系统生成的对象键保存原始文件。"""
        ...

    async def get(self, object_key: str) -> bytes:
        """读取完整原始文件，供同步解析或索引重建使用。"""
        ...

    async def delete(self, object_key: str) -> None:
        """删除一个明确对象键对应的原始文件。"""
        ...


class LLMClient(Protocol):
    """文本生成模型边界，不承担检索或 Prompt 上下文构造。"""

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """基于系统约束和用户消息生成非空文本答案。"""
        ...

    def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """按模型实际产生的增量顺序返回非空文本片段。

        流式能力属于模型端口而不是 HTTP 层：这样 Route 只负责把增量编码为传输协议，
        不需要知道 OpenAI-Compatible SDK 的 Chunk 结构，也不会用假逐字动画掩盖模型延迟。
        """
        ...


class QueryRewriter(Protocol):
    """把含糊用户问题改写为至多一个更适合检索的查询。"""

    async def rewrite(self, query: str) -> str | None:
        """返回保留原意的替代查询；无需改写时返回 ``None``。"""
        ...


class Reranker(Protocol):
    """使用查询与候选全文的相关性进行第二阶段排序。"""

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        top_n: int,
    ) -> list[RerankResult]:
        """返回不超过 ``top_n`` 个候选 ID 与请求内相关性分数。"""
        ...


class OCRClient(Protocol):
    """把图片字节转换为纯文本的视觉 OCR 边界。"""

    async def extract_text(self, image: bytes, mime_type: str) -> str:
        """识别单张可信格式图片，返回保持阅读顺序的非空文本。"""
        ...


class VisionClient(Protocol):
    """把图表、示意图等视觉内容转换为可检索文字的模型边界。"""

    async def describe(self, image: bytes, mime_type: str, caption: str = "") -> str:
        """提取可见文字、结构和图形语义，不推断图片中不存在的事实。"""
        ...
