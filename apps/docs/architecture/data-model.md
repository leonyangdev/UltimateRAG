# 核心领域模型

这一页介绍 `domain/models.py` 里定义的核心领域对象。**这些 dataclass 是整个系统传递业务语义的「货币」**，它们在应用层、适配器、API 之间流动，且不绑定任何框架。

代码位置：`src/ultimate_rag/domain/models.py`

## 1. 领域模型全家福

```text
KnowledgeBase（知识库）
    └── Document（文档）
         ├── 状态：DocumentStatus
         ├── 原文件：MinIO ObjectKey
         └── 后台任务：IngestionJob
              ├── Chunk[]（文本片段，存 PostgreSQL）
              │     └── 每个 Chunk 带 SourceLocator（来源定位）
              └── Milvus 向量（派生）
```

## 2. 状态枚举

### DocumentStatus（文档状态，面向用户）

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
                                    任一环节失败 → FAILED
```

`README` 之前所有状态都代表「尚未完成处理」，绝不能被检索。

### IngestionJobStatus（任务状态，面向调度）

```text
PENDING → RUNNING → SUCCEEDED / FAILED
```

::: tip 为什么有两种状态
文档状态表达「处理到哪一步」，面向用户展示；
任务状态表达「Worker 的领取/重试/完成语义」，面向调度。
二者刻意分开，避免把队列实现细节暴露到 API。
:::

### BlockType（语义块类型）

```text
HEADING（标题） / TEXT（正文） / CODE（代码）
LIST（列表）   / QUOTE（引用） / TABLE（表格） / IMAGE（图片）
```

## 3. 核心对象逐个说明

### KnowledgeBase —— 知识库

文档和检索范围的**顶层容器**。字段：`id`、`name`、`description`、`created_at`、`updated_at`。

### Document —— 文档元数据

描述一份上传的原始文档及其处理状态：

| 字段 | 含义 |
|---|---|
| `id` | 系统生成的 UUID |
| `knowledge_base_id` | 所属知识库 |
| `filename` | 展示用文件名（已净化 basename） |
| `mime_type` / `extension` | 客户端声明，用于 Parser 路由 |
| `object_key` | MinIO 对象键（系统生成） |
| `sha256` | 内容指纹 |
| `status` | 当前处理状态 |
| `parser_name` / `parser_version` | 实际使用的解析器 |
| `error_message` | 失败原因（可操作） |

### SourceLocator —— 来源定位器（★ 关键设计）

记录「一段内容来自原始文件的哪里」。字段全部可选，因为不同格式定位精度不同：

| 字段 | 使用场景 |
|---|---|
| `heading_path` | Markdown / Word / PPT / HTML / PDF：章节路径 |
| `page` | PDF：页码 |
| `bbox` | PDF：坐标框（左上角原点） |
| `sheet` / `cell_range` | Excel：工作表 + 单元格区域 |
| `slide` | PPT：幻灯片序号 |

它贯穿整个链路，最终形成 Citation：

```text
Block → Chunk → PostgreSQL/Milvus → RetrievalResult → Citation → 前端展示
```

### Block —— 解析后的最小语义块

Parser 的输出单元：`id`（稳定 UUID5）、`type`（BlockType）、`content`、`locator`、`metadata`。

### ParsedDocument —— 统一解析结果

`document_id + blocks[] + metadata`。**这是所有 Parser 的统一出口**，下游不再感知原始格式。

### Chunk —— 可检索文本单元

真正被向量化、检索、引用的单元：

| 字段 | 含义 |
|---|---|
| `id` | 稳定 ID（由文档ID+序号+内容生成，幂等） |
| `knowledge_base_id` / `document_id` | 归属 |
| `index` | 在文档中的顺序 |
| `content` | 文本内容（含标题前缀） |
| `heading_path` | 章节路径（用于检索展示与 Citation） |
| `token_count` | Token 数（与实际切分同一 Tokenizer） |
| `locator` | 来源定位 |
| `metadata` | 切块策略、来源标签、文件名、V3 `parent_id/parent_child_*` 等 |

### EmbeddedChunk —— Chunk + 向量

`chunk + embedding`。写入 Milvus 前的组合。

### RetrievalResult —— 检索命中

除基础来源字段外，V3 还保留 `dense_score / sparse_score / fusion_score / rerank_score`、
`retrieval_sources`、`matched_content` 和 `context_chunk_ids`。`score` 表示当前最终排序实际使用的
分数，不能跨模式或跨请求直接比较。

保留完整来源信息，**检索结果可以直接构造 Citation，无需再查库**（避免 N+1）。

### Citation —— 面向 API 的引用

`document_id / filename / chunk_id / heading_path / locator / context_chunk_ids`。不暴露向量库内部字段，
并同时保留精确命中锚点与 Small2Big 实际上下文范围。

### RetrievalOptions / RetrievalTrace / RetrievalRun

- `RetrievalOptions`：一次请求的模式、候选宽度、阶段开关和文档白名单
- `RetrievalTrace`：查询变体、候选/结果数、实际执行阶段和降级原因
- `RetrievalRun`：不可变的 `results + trace`，供 Explain API 与 RAG 共用

### IngestionJob —— 后台任务快照

Worker 处理的任务：`id / document_id / status / attempts / max_attempts / available_at / locked_at / worker_id / error_message`。

## 4. 数据在哪些层如何变化

```text
原始文件字节
   ↓ Parser
ParsedDocument + Block[]        （Domain 模型）
   ↓ Chunker
Chunk[]                         （Domain 模型）
   ↓ Embedder
EmbeddedChunk[]                 （Domain 模型）
   ↓ Milvus Dense + BM25 → RRF → Rerank → Small2Big
检索 → RetrievalRun             （RetrievalResult[] + RetrievalTrace）
   ↓ ContextBuilder
上下文文本                       （普通字符串）
   ↓ LLM
答案 + Citation[]               （Domain 模型）
```

> 注意到没有？从 ParsedDocument 到 RetrievalResult，全程都是**不可变 dataclass**（`frozen=True`）。这保证了数据在跨层传递时的安全性，也让测试非常方便。

## 5. 端口（Protocol）—— 可替换能力的契约

`domain/ports.py` 只为明确存在多实现或需要隔离外部依赖的能力定义小端口。下面是核心片段：

```python
class DocumentParser(Protocol):
    name: str
    version: str
    def supports(self, source: DocumentSource) -> bool: ...
    async def parse(self, source: DocumentSource) -> ParsedDocument: ...

class Embedder(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_query(self, query: str) -> list[float]: ...

class VectorStore(Protocol):
    async def ensure_collection(self) -> None: ...
    async def upsert(self, chunks: Sequence[EmbeddedChunk]) -> None: ...
    async def upsert_sparse(self, chunks: Sequence[Chunk]) -> None: ...
    async def search(self, query_vector, knowledge_base_id, top_k) -> list[RetrievalResult]: ...
    async def search_sparse(self, query, knowledge_base_id, top_k) -> list[RetrievalResult]: ...
    async def delete_by_document(self, document_id: str) -> None: ...
    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None: ...

class QueryRewriter(Protocol):
    async def rewrite(self, query: str) -> str | None: ...

class Reranker(Protocol):
    async def rerank(self, query, candidates, top_n) -> list[RerankResult]: ...

class LLMClient(Protocol):
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...
    def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]: ...
```

### DocumentSource —— Parser 的输入

交给 Parser 的「原始文档快照」：`document_id / filename / mime_type / content`。不包含任何基础设施句柄。

## 6. 业务异常

`domain/exceptions.py` 定义了可预期的业务异常，接口层依据它们生成稳定的 HTTP 状态码：

| 异常 | HTTP | 场景 |
|---|---|---|
| `ResourceNotFoundError` | 404 | 知识库/文档不存在 |
| `InvalidDocumentError` | 400 | 上传文件不合法 |
| `UnsupportedDocumentTypeError` | 400 | 没有可用的 Parser |
| `DocumentBusyError` | 409 | 文档正在后台处理，暂不能删除 |
| `DocumentProcessingError` | 502 | 解析/切块/向量化/索引失败 |
| `ExternalServiceError` | 502 | 外部服务故障 |

## 下一步

- 想理解三大存储各自保存什么 → [三大存储职责](/architecture/data-stores)
- 想深入某个模块的实现 → [模块总览](/modules/index)
