# Domain 领域层

代码位置：`src/ultimate_rag/domain/`

## 1. 这一层是什么

Domain 层是整个项目的**地基**，包含三类东西：

1. **领域模型**（`models.py`）：`Document`、`Block`、`Chunk` 等业务对象的定义
2. **端口契约**（`ports.py`）：可替换能力的接口（`DocumentParser`、`Embedder`、`VectorStore`…）
3. **业务异常**（`exceptions.py`）：`ResourceNotFoundError`、`InvalidDocumentError` 等

## 2. 铁律：不依赖任何框架

Domain 层**禁止导入** FastAPI、SQLAlchemy、Milvus、OpenAI SDK、LangChain 等任何外部框架。

为什么？因为 Domain 是系统的核心，如果它绑定了某个框架，换框架 = 重写核心。

```python
# ✅ 领域模型是纯 dataclass
@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    content: str
    ...

# ❌ 绝不这样
# from langchain_core.documents import Document
```

## 3. 三个文件各自的职责

### models.py —— 领域模型

定义所有业务对象（详见 [核心领域模型](/architecture/data-model)）：

- 状态枚举：`DocumentStatus`、`IngestionJobStatus`、`BlockType`
- 值对象：`SourceLocator`（来源定位）
- 实体：`KnowledgeBase`、`Document`、`IngestionJob`
- 中间结果：`DocumentSource`、`Block`、`ParsedDocument`
- 检索/引用：`Chunk`、`EmbeddedChunk`、`RetrievalResult`、`Citation`、`DocumentPreview`
- 会话：`ChatSession`、`ChatMessage`、`ChatTurn`

所有对象都是 `frozen=True`（不可变），保证跨层传递安全、便于测试。

### ports.py —— 端口契约

用 `typing.Protocol` 定义可替换能力的最小接口。**应用层依赖这些端口，基础设施层实现这些端口。**

```python
class DocumentParser(Protocol):
    name: str
    version: str
    def supports(self, source: DocumentSource) -> bool: ...
    async def parse(self, source: DocumentSource) -> ParsedDocument: ...

class Embedder(Protocol):
    async def embed_documents(self, texts) -> list[list[float]]: ...
    async def embed_query(self, query) -> list[float]: ...

class VectorStore(Protocol):
    async def ensure_collection(self) -> None: ...
    async def upsert(self, chunks) -> None: ...
    async def upsert_sparse(self, chunks) -> None: ...
    async def search(self, query_vector, knowledge_base_id, top_k) -> list[RetrievalResult]: ...
    async def search_sparse(self, query, knowledge_base_id, top_k) -> list[RetrievalResult]: ...
    async def delete_by_document(self, document_id) -> None: ...
    async def delete_by_knowledge_base(self, knowledge_base_id) -> None: ...
```

V3 端口一览：

| 端口 | 方法 | V3 实现 |
|---|---|---|
| `DocumentParser` | `supports` / `parse` | 7 种 Parser |
| `Chunker` | `split` | `StructureAwareChunker` |
| `Embedder` | `embed_documents` / `embed_query` | `BailianEmbedder` |
| `VectorStore` | Dense/Sparse `upsert` / `search` / `delete_*` | `MilvusVectorStore` |
| `QueryRewriter` | `rewrite` | `BailianQueryRewriter` |
| `Reranker` | `rerank` | `BailianReranker` |
| `ObjectStorage` | `ensure_bucket` / `put` / `get` / `delete` | `MinioObjectStorage` |
| `LLMClient` | `generate` / `stream` | `BailianLLMClient` |
| `OCRClient` | `extract_text` | `BailianOCRClient` |
| `VisionClient` | `describe` | `BailianVisionClient` |
| `PDFPreviewRenderer` | `render` | `PDFiumPreviewRenderer` |

::: tip 为什么用 Protocol 而不是抽象类
Protocol 是「鸭子类型」接口：只要类实现了这些方法签名，就自动满足接口，不需要继承。这让测试时可以轻松注入内存 Stub（假实现），不访问真实外部服务。
:::

### exceptions.py —— 业务异常

定义「可预期」的业务异常，接口层依据它们返回稳定的 HTTP 状态码（见 [核心领域模型](/architecture/data-model#_6-业务异常)）。

```python
class UltimateRAGError(Exception): ...
class ResourceNotFoundError(UltimateRAGError): ...   # → 404
class InvalidDocumentError(UltimateRAGError): ...     # → 400
class DocumentBusyError(UltimateRAGError): ...        # → 409
class DocumentProcessingError(UltimateRAGError): ...  # → 502
```

## 4. 典型代码导读

看一段最核心的领域模型 `SourceLocator`，理解「跨格式定位」是怎么设计的：

```python
@dataclass(frozen=True, slots=True)
class SourceLocator:
    """跨文档格式的来源位置，供 Chunk、检索结果和 Citation 统一复用。"""
    heading_path: tuple[str, ...] = ()          # 章节路径（Markdown/Word/PDF）
    page: int | None = None                     # 页码（PDF）
    bbox: tuple[float, float, float, float] | None = None  # 坐标框（PDF）
    sheet: str | None = None                    # 工作表（Excel）
    cell_range: str | None = None               # 单元格区域（Excel）
    slide: int | None = None                    # 幻灯片序号（PPT）

    def to_metadata(self) -> dict[str, JsonValue]:
        """转换为 PostgreSQL JSONB、Milvus JSON 和 API 都可接受的稳定字典。"""
        ...
```

设计要点：

- 字段**全部可选**，因为不同格式定位精度不同
- 提供 `to_metadata()` / `from_metadata()` 完成「强类型对象 ⇄ 可持久化字典」的双向转换
- 提供 `display()` 生成面向用户/Prompt 的定位文本（如「RAG / Embedding · 第 12 页」）

## 5. 常见问题

**Q：为什么 Chunk 的 `id` 要「稳定」？**
A：Chunk ID 由「文档ID + 序号 + 内容」用 UUID5 生成。只要内容和顺序不变，重试处理得到的 ID 就一样，所以向量 Upsert 是幂等的，不会产生重复数据。这是整个系统幂等性的基础。

**Q：为什么领域对象要 frozen（不可变）？**
A：数据在应用层、适配器、API 之间传递时，不可变对象不会被意外修改；同时可以放心地在不同线程/任务间共享。

## 下一步

- 领域模型被谁使用？→ [Application 应用层](/modules/application)
- 想看看一个具体端口怎么被实现 → [Parser 解析器](/modules/parsers) 或 [VectorStore 向量库](/modules/vectorstore)
