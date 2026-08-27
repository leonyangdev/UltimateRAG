# UltimateRAG — AI 开发规范

本文档用于约束所有参与 UltimateRAG 项目开发的 AI 编程 Agent，包括但不限于：

- Codex
- Claude Code
- Zcode
- 其他 AI 编程 IDE / Agent

除非某个子目录下存在更具体的 `AGENTS.md`，否则本规范适用于整个仓库。

本项目追求的不是“代码看起来高级”，而是代码：

- 简单
- 清晰
- 正确
- 健壮
- 易测试
- 易维护
- 易调试
- 易理解
- 易扩展

当存在多种实现方式时，优先选择：

> **长期心智负担最低的实现。**

---

# 1. 项目定位

UltimateRAG 是一个持续演进的企业级 RAG 平台。

项目按照大版本逐步演进：

```text
V1.0  Naive RAG
V2.0  Document Intelligence
V3.0  Advanced Retrieval
V4.0  Enterprise RAG
V5.0  RAGOps
V6.0  Intelligent RAG
```

开发时必须遵守当前版本的范围。

不要因为未来可能需要某项能力，就提前实现未来版本中的复杂功能。

例如 V1.0 不应因为未来可能使用：

```text
Kafka
LangGraph
OCR Worker
Kubernetes
GraphRAG
```

就提前引入这些能力。

---

# 2. 编码前必须先理解项目

进行任何非简单修改之前，必须：

1. 阅读当前 `AGENTS.md`
2. 阅读相关代码
3. 阅读 `docs/` 下相关产品文档和架构文档
4. 搜索仓库中是否已经存在类似实现
5. 理解现有调用链
6. 理解当前模块职责
7. 明确本次修改范围

重要项目文档包括：

```text
docs/product/
docs/architecture/
docs/adr/
docs/evaluation/
```

不要在不了解现有实现的情况下直接重写模块。

如果：

```text
代码实现
```

和：

```text
架构文档
```

存在冲突，应先指出问题，再决定应该调整哪一方。

不要静默改变既有架构设计。

---

# 3. 核心开发理念

## 3.1 简单优先

优先使用：

> 简单、直接、容易理解的实现。

不要为了体现技术能力而主动引入复杂设计。

除非当前问题确实需要，否则避免：

- 复杂设计模式
- 多层继承
- 元编程
- 大量 Decorator
- 复杂 Factory
- 通用 Framework
- Event Bus
- Dependency Injection Framework
- 分布式架构
- 复杂异步流程
- 多余的中间抽象层

例如：

```python
parser = parser_registry.resolve(source)
document = await parser.parse(source)
```

如果已经足够解决问题，就不要继续抽象成：

```text
Factory
→ Provider
→ Resolver
→ Dispatcher
→ Handler
→ Adapter
→ Parser
```

---

# 4. 不要炫技

不要为了展示：

```text
设计模式
高级 Python 技法
函数式编程
复杂泛型
元编程
异步技巧
架构技巧
```

而降低代码可读性。

代码不是写给面试官炫技的。

代码是写给未来维护者看的。

优先：

```python
if document.status == DocumentStatus.READY:
    return document
```

而不是为了缩短代码写成晦涩表达式。

---

# 5. 降低开发者心智负担

代码应该让开发者能够：

> 从上往下阅读，就理解主要业务流程。

例如文档处理流程：

```text
validate
↓
load
↓
parse
↓
chunk
↓
embed
↓
index
```

应该尽可能保持清晰。

不要把一个简单工作流隐藏在：

```text
多个 Decorator
多个 Hook
多个事件
多层 Callback
多个抽象 Factory
```

之后。

重要业务流程应该显式。

---

# 6. 禁止过度设计

不要为尚未发生的问题设计复杂解决方案。

例如：

当前只有同步文档处理：

```text
Parse
→ Chunk
→ Embed
→ Index
```

不要立刻引入：

```text
Kafka
Worker Cluster
Distributed Task Scheduler
```

当前只有简单 RAG：

```text
Retrieve
→ Context
→ LLM
```

不要为了“这是 AI 项目”就使用 LangGraph。

当前只有一个 Parser：

```text
MarkdownParser
```

可以提前定义合理 `DocumentParser` 接口，

但不要第一天就构建完整插件市场、动态热加载、远程插件运行时。

原则：

> **解决今天真实存在的问题，同时为已经明确的未来扩展保留合理接口。**

而不是：

> 提前实现一个还没有真实需求的未来系统。

---

# 7. 每个抽象都必须证明自己的价值

新增：

```text
Interface
Adapter
Factory
Manager
Registry
Provider
Service
Repository
```

之前必须思考：

> 它到底解决了什么实际问题？

合理的抽象：

```text
DocumentParser
Embedder
VectorStore
LLMClient
```

因为这些能力未来明确存在多种实现。

不合理的抽象：

> 只为了“分层完整”而套一层没有实际逻辑的 Wrapper。

核心原则：

> **每一个新增的抽象层，都必须有明确存在价值。**

---

# 8. 架构边界

UltimateRAG 应保持清晰的依赖方向：

```text
Interface
    ↓
Application
    ↓
Domain
    ↑
Infrastructure
```

核心业务逻辑不应该直接依赖基础设施细节。

基础设施包括：

```text
FastAPI
SQLAlchemy
PostgreSQL
MinIO
Milvus
OpenAI SDK
BGE
MinerU
LangChain
LangGraph
```

这些应该尽可能处于外围。

---

# 9. Domain 层必须保持独立

不要让 UltimateRAG 的领域模型绑定某个框架。

例如不要把：

```python
from langchain_core.documents import Document
```

作为整个项目的核心文档数据结构。

UltimateRAG 应拥有自己的领域模型：

```text
Document
ParsedDocument
Block
Chunk
RetrievalResult
Citation
```

如果需要 LangChain：

```text
UltimateRAG Domain Model
        ↓
LangChain Adapter
        ↓
LangChain
```

而不是：

```text
UltimateRAG
=
LangChain Data Model
```

---

# 10. 核心可替换能力

对于明确存在多实现可能的模块，可以定义清晰的小接口。

当前主要扩展点：

```text
DocumentParser
Chunker
Embedder
VectorStore
LLMClient
```

未来可能包括：

```text
Retriever
Reranker
QueryRewriter
Evaluator
```

不要因为“以后也许会换”就给所有类创建 Interface。

只有以下情况才考虑抽象：

1. 当前已有多个实现
2. 产品架构明确要求可替换
3. 抽象明显提升测试性
4. 抽象可以隔离外部依赖

---

# 11. 文档处理架构

RAG Core 不应该关心文档原始格式。

例如：

```text
Markdown
PDF
DOCX
XLSX
PPTX
HTML
OCR
Image
```

应该统一经过：

```text
DocumentSource
      ↓
DocumentParser
      ↓
ParsedDocument
      ↓
Block[]
```

后续：

```text
Chunk
Embedding
Index
Retrieval
```

只依赖统一模型。

不要在业务代码各处散落：

```python
if extension == ".pdf":
    ...
elif extension == ".docx":
    ...
elif extension == ".md":
    ...
```

应优先：

```python
parser = parser_registry.resolve(source)
parsed_document = await parser.parse(source)
```

---

# 12. 数据职责必须清晰

## PostgreSQL

PostgreSQL 保存：

> 业务事实数据和元数据。

例如：

```text
KnowledgeBase
Document
DocumentStatus
Chunk Metadata
Configuration
```

---

## MinIO

MinIO 保存：

> 原始文件和对象数据。

例如：

```text
Markdown
PDF
DOCX
XLSX
PPTX
Image
```

---

## Milvus

Milvus 保存：

> 向量索引和检索数据。

Milvus 是：

> **派生索引**

不是业务事实数据源。

理论上：

```text
MinIO
+
PostgreSQL
```

应该可以重新构建 Milvus 索引。

不要把业务状态只保存在 Milvus。

---

# 13. 模块职责必须明确

建议按照职责组织：

```text
domain/
application/
infrastructure/

parsers/
chunkers/
embeddings/
vectorstores/
retrieval/
generation/
services/
```

避免创建成为垃圾桶的：

```text
utils.py
helpers.py
common.py
misc.py
manager.py
```

除非其中内容确实高度相关。

命名优先使用：

```text
DocumentRepository
MarkdownParser
EmbeddingService
MilvusVectorStore
ContextBuilder
```

避免：

```text
DataManager
Processor
Handler
CommonHelper
Utils
```

这种含义过于模糊的名称。

---

# 14. 函数设计

每个函数应该有一个明确职责。

优先使用提前返回，减少嵌套。

推荐：

```python
if not document:
    raise DocumentNotFoundError(document_id)

if document.status != DocumentStatus.READY:
    raise DocumentNotReadyError(document_id)

return await retriever.search(query)
```

避免：

```python
if document:
    if document.status == DocumentStatus.READY:
        if ...
            if ...
```

不要机械规定函数不能超过多少行。

函数是否拆分，应以：

> 是否更容易理解

为判断标准。

不要把一个简单函数拆成十几个两三行的小函数，反而增加跳转成本。

---

# 15. 命名规范

名称应该尽可能表达业务含义。

推荐：

```python
knowledge_base_id
parsed_document
retrieval_results
embedding_vectors
document_repository
```

避免：

```python
data
info
obj
res
tmp
val
x
item2
```

除非作用域非常小且语义显而易见。

Boolean 变量推荐：

```python
is_ready
has_permission
should_reindex
supports_streaming
```

---

# 16. 代码注释规范

本节面向在 UltimateRAG 中工作的 AI 编码助手和人类协作者，用于统一生产代码、测试代码和脚本中的注释标准。

任何新增或修改代码的行为都必须遵守本节。

## 16.0 背景与目标

UltimateRAG 具有双重属性：

1. **企业级项目**：代码会投入实际业务，需要长期可维护、可交接、可审计
2. **学习型项目**：项目用于系统学习 RAG 原理和企业工程实践，注释同时承担“教材”职责

编写注释时，应假设读者可能是：

```text
半年后已经忘记实现细节的作者
正在学习 RAG、但具备基础 Python 能力的开发者
需要快速理解系统调用链的新同事
需要审查安全性、数据一致性和失败行为的维护者
```

因此，本项目的注释详尽度必须高于普通生产项目的默认标准。

核心原则：

> **优先解释为什么这样设计，以及这种设计解决了什么问题。**

同时，由于本项目具有学习属性，复杂流程还必须解释：

> **当前代码处于哪个业务阶段、数据如何变化、前后步骤如何衔接。**

“注释解释 why”不等于完全禁止解释 what。以下内容即使属于 what，也应在复杂流程中明确说明：

```text
当前处理阶段
领域数据的含义
外部 SDK 返回结构
状态机如何推进
异常由哪一层接管
某段算法的输入和输出
```

禁止只给函数写 Docstring，却让函数内部几十行复杂逻辑完全没有注释。Docstring 负责整体契约，函数内部注释负责实现过程，两者不能互相替代。

## 16.1 文件与模块级 Docstring

每个非平凡 Python 模块都必须在文件顶部提供模块级 Docstring。以下 RAG 核心模块需要更详细的说明：

```text
parsers/
chunkers/
embeddings/
vectorstores/
retrieval/
generation/
evaluation/
application/services.py
外部基础设施适配器
```

模块级 Docstring 应根据实际情况说明：

1. **模块职责**：负责什么
2. **架构边界**：依赖什么、不负责什么、不能被什么反向依赖
3. **设计背景**：为什么选择当前方案
4. **典型调用位置**：位于哪条 Pipeline 的哪个阶段
5. **重要依赖**：使用了哪些不直观的框架、协议或外部服务
6. **关键约束**：批量上限、超时、重试、模型输入、存储一致性、安全或成本约束
7. **已知限制**：当前版本真实存在且会影响使用的限制

推荐格式：

```python
"""结构感知 Markdown 切块模块。

模块职责：
    把统一的 ParsedDocument 切分为适合 Embedding 和 Retrieval 的 Chunk，
    同时保留标题路径与稳定 Chunk ID。

架构边界：
    本模块只依赖 UltimateRAG 领域模型，不负责解析原始文件、调用 Embedding API
    或写入向量数据库。

设计背景：
    V1 优先按 Markdown 标题聚合章节，仅在章节超出字符预算时继续切分。
    相比对整篇文档直接使用固定窗口，这种方式更容易保留局部语义和来源定位。

典型调用位置：
    IngestionService 在 Parser 之后、Embedder 之前调用本模块。

已知限制：
    V1 使用字符数而非模型 Tokenizer 控制大小，token_count 只用于观察。
"""
```

以下示例展示未来多格式企业文档切块模块如何完整描述职责、设计背景、依赖、使用场景和已知限制：

```python
"""
模块职责：负责将原始文档切分为语义完整的文本块（chunk）。

设计背景：
    企业文档类型多样（PDF 报告、Word 合同、Excel 表格、Confluence 页面等），
    统一使用固定长度切分会破坏表格和列表的语义完整性，因此本模块按文档类型
    分流到不同的切分策略。

依赖：
- langchain-text-splitters：提供基础的递归字符切分能力
- unstructured：用于识别文档结构（标题、表格、列表）

典型使用场景：
    ingestion pipeline 在文档解析之后、向量化之前调用本模块。

注意事项 / 已知限制：
- 超大表格（>2000 token）目前会被截断，后续需要引入表格摘要策略（见 TODO）
"""
```

这个示例用于说明注释的结构和详尽程度，不代表 V1 已经支持其中列出的文件格式、依赖或表格策略。实际使用时必须根据当前模块的真实实现改写所有内容，禁止直接复制不存在的能力、依赖、限制或 TODO。

要求：

- “设计背景”是重点。存在真实备选方案时，应说明选择当前方案的理由和放弃其他方案的原因
- 只记录实际采用或确实评估过的方案，禁止为了显得完整而编造技术选型过程
- 涉及外部服务时，应说明当前代码实际实现的 Timeout、Retry、Batch、数据映射和失败行为
- 费用会随供应商变化时，不在代码注释中硬编码价格；说明哪些调用会产生费用以及采用了什么控制策略
- 简单的 `__init__.py`、纯导出模块或一目了然的常量模块可以使用简短 Docstring，不机械套完整模板
- 已知限制必须是当前版本真实存在的事实；未来规划应进入产品文档、架构文档或 ADR

## 16.2 类与函数 Docstring

Python 统一使用 Google 风格 Docstring。

### 类 Docstring

核心类、领域接口、应用服务和基础设施适配器必须说明：

1. 该类在整体 RAG 架构中的位置
2. 该类承担的职责
3. 该类明确不承担的职责
4. 生命周期或状态特征，例如是否无状态、是否复用外部客户端
5. 关键设计选择与失败行为

### 函数 Docstring

公共函数、公共方法、API 入口和 Pipeline 入口必须提供完整 Docstring。私有函数可以根据复杂度简化，但核心算法、数据转换或一致性逻辑不能省略。

函数 Docstring 应根据实际需要包含：

```text
一句话行为摘要
设计原因或业务背景
Args
Returns
Raises
Side Effects
复杂度
重要限制
```

不是所有函数都必须机械填写每个小节：

- 没有返回值时不需要空的 `Returns`
- 不会主动抛出业务异常时不需要空的 `Raises`
- 只有算法复杂度不直观或会影响数据规模选择时才写复杂度
- 存在数据库写入、状态修改、外部调用或缓存变化时，应说明重要 Side Effect

推荐：

```python
async def ingest(
    self,
    knowledge_base_id: str,
    filename: str,
    content: bytes,
) -> Document:
    """同步摄取文档，并仅在全部索引步骤成功后将其标记为 READY。

    原始文件会先于解析和索引持久化。这样即使后续步骤失败，仍可使用原文件
    排查问题或重新构建作为派生数据的向量索引。

    Args:
        knowledge_base_id: 文档所属知识库 ID。
        filename: 用于展示和类型判断的原始文件名，不用于构造对象存储路径。
        content: 已完成 API 请求体读取的原始字节。

    Returns:
        已完成处理且状态为 READY 的文档领域对象。

    Raises:
        InvalidDocumentError: 文件类型、编码、大小或内容不符合要求。
        DocumentProcessingError: 解析、切块、向量化或索引阶段失败。

    Side Effects:
        写入 MinIO、PostgreSQL 和 Milvus；处理失败时保留原文件并记录 FAILED。
    """
```

涉及以下参数时，必须解释默认值或配置值的来源与取舍：

```text
chunk size
chunk overlap
embedding dimension
batch size
top_k
similarity threshold
rerank top_n
temperature
max tokens
context budget
timeout
retry count
```

来源可以是：

```text
模型或供应商的硬限制
产品需求
安全边界
公开文档
Benchmark 或评估实验
当前版本的明确简化策略
```

禁止只写“经验值”。如果当前数值尚无实验依据，应如实说明它是可调整的 V1 默认值，并指出应通过哪类评估校准；不能编造实验结果、模型限制或业务依据。

## 16.3 函数内部块级注释

当函数包含较长或不直观的逻辑时，必须添加块级注释，使读者能够从上到下理解整个流程，而不需要先逐行逆向推断。

出现以下任一情况时，通常必须添加函数内部注释：

1. 包含多个连续处理阶段，例如 Parse、Chunk、Embed、Index
2. 存在重要条件分支、提前返回、Retry、Rollback 或补偿逻辑
3. 存在领域模型、ORM 模型和外部 API 数据之间的转换
4. 涉及状态变化、事务、跨存储一致性或幂等
5. 调用了行为不直观的框架、SDK、算法或外部服务
6. 为安全、性能、兼容性或边界情况采用了看似多余的实现
7. 包含排序、过滤、归一化、打分、窗口切分或文本处理算法
8. 连续代码较长，仅靠变量名无法快速看出阶段边界

块级注释优先说明：

```text
当前阶段的目标
输入数据在此阶段如何变化
为什么采用当前顺序
重要分支分别代表什么业务含义
失败后留下什么状态
异常由当前层处理还是继续上抛
容易被误删或误改的安全与一致性约束
```

### 16.3.1 空行与视觉结构

函数内部注释必须具有清晰的视觉结构：

1. 函数 Docstring 结束后保留一个空行，再开始第一个内部注释块或逻辑阶段
2. 每个相对独立的逻辑阶段前放置一个注释块
3. 上一个逻辑阶段与下一个注释块之间保留一个空行
4. 注释块必须紧贴它所解释的代码，注释与代码之间不要插入空行
5. 同一阶段内紧密相关的代码保持连续，不要用过多空行打断
6. 一个注释块通常控制在 1～4 行；内容较多时拆成多个有层次的逻辑阶段
7. 不要把十几行解释堆成一面“注释墙”；长篇背景应放入 Docstring 或架构文档
8. 线性 Pipeline 可以使用“阶段 1 / 阶段 2”帮助学习，但步骤经常调整时优先使用稳定的语义标题

推荐：

```python
async def process(document: Document) -> None:
    """完成文档解析、切块、向量化和索引。"""

    # 阶段 1：把原始格式转换为统一领域模型。
    # Parser 之后的步骤只依赖 ParsedDocument，不再判断 Markdown、PDF 等文件格式。
    parsed_document = await parser.parse(document.source)

    # 阶段 2：按语义结构生成 Chunk，并保留标题路径用于 Citation。
    # 空结果代表文档没有可索引内容，不能继续调用会产生费用的 Embedding API。
    chunks = await chunker.split(parsed_document)
    if not chunks:
        raise EmptyDocumentError(document.id)

    # 阶段 3：批量向量化，避免每个 Chunk 发起一次外部网络请求。
    # Embedder 负责按照供应商上限继续分批，并保持输入和输出顺序一致。
    vectors = await embedder.embed_documents([chunk.content for chunk in chunks])

    # 阶段 4：Milvus 是可重建的派生索引；只有写入成功后才能把文档标记为 READY。
    await vector_store.upsert(chunks, vectors)
    await document_repository.mark_ready(document.id)
```

不推荐：

```python
async def process(document: Document) -> None:
    """处理文档。"""
    # 解析文档
    parsed_document = await parser.parse(document.source)
    # 切块
    chunks = await chunker.split(parsed_document)
    # 获取内容
    texts = [chunk.content for chunk in chunks]
    # 生成向量
    vectors = await embedder.embed_documents(texts)
    # 保存向量
    await vector_store.upsert(chunks, vectors)
```

不推荐示例的问题包括：

- Docstring 没有说明行为边界
- 不同处理阶段之间没有空行，视觉上挤成连续代码墙
- 注释只是把函数名翻译成中文
- 没有解释顺序、失败行为、批处理或状态一致性

简单且一目了然的函数不要求为了“注释覆盖率”补充噪音。判断标准不是机械行数，而是尚不熟悉当前模块的开发者能否顺畅理解其目的、阶段和关键约束。

## 16.4 RAG 核心环节的加强要求

以下环节具有最高学习价值，也最容易出现隐蔽错误，注释要求高于普通 CRUD 代码：

| 环节 | 必须解释的内容 |
|---|---|
| Parsing | 原格式如何映射为统一领域模型、保留或丢弃了哪些结构、编码和格式限制 |
| Chunking | 切分策略选择依据、Chunk 大小、Overlap 原因、标题或表格等结构如何保留 |
| Embedding | 模型选型、维度、语言支持、是否归一化、Batch 策略、输入限制、费用与失败边界 |
| Indexing / VectorStore | Schema 含义、主键稳定性、距离度量、索引类型、一致性和幂等策略 |
| Retrieval | Cosine / Dot Product / L2 的选择依据、top_k、过滤条件、召回结果如何映射 |
| Rerank | 为什么需要精排、候选集如何压缩、模型和 top_n 的选择依据 |
| Context Building | 上下文预算如何分配、截断顺序、来源编号和去重策略 |
| Prompt / Generation | Prompt 结构、模型参数、知识边界、Prompt Injection 防护和无证据降级行为 |
| Citation / Hallucination | 答案如何关联来源、低置信度或无召回时如何避免编造 |
| Evaluation | 指标定义、选择原因、数据集来源、阈值和回归基线 |

如果某项能力当前版本尚未实现，不要为了满足表格而添加假注释。应在实现该能力时遵循相应要求。

## 16.5 技术选择、超参数与“踩坑记录”

### 技术选择

存在多种合理实现时，注释或关联文档应回答：

```text
最终选择了什么
解决了什么当前问题
为什么没有采用主要备选方案
这个选择带来了什么代价或限制
什么条件变化后需要重新评估
```

重大架构决策应写入 ADR，代码注释只保留与当前实现直接相关的简短摘要并引用 ADR。

### 超参数

“魔法数字”和重要默认参数必须说明来源。若来源于外部模型或服务限制，应确保内容与当前依赖版本一致；若可能频繁变化，优先链接项目文档，不要复制容易过期的大段供应商说明。

### 踩坑记录

鼓励记录真正有助于避免回归的踩坑经验，但必须满足：

1. 问题在本项目中真实发生过，或有可靠测试、Issue、Benchmark、ADR 支撑
2. 说明原方案为什么失败，以及当前代码如何规避
3. 能通过测试表达的行为，应优先添加 Regression Test
4. 禁止 AI 编造“最初发现”“线上发生过”“实验表明”等虚假历史

推荐：

```python
# NOTE: Milvus 删除后物理 row_count 可能暂时包含等待 Compaction 的 Tombstone。
# 业务可见性测试必须使用强一致 Query/Search，不能把物理行数直接当作有效 Chunk 数。
await client.flush(collection_name=collection_name)
```

## 16.6 行内注释与标记规范

行内注释适用于：

- 非显而易见的算法步骤
- 重要业务规则
- 容易误删的安全校验
- 数据结构或单位不直观的值
- 正则表达式、位运算、协议字段和 SDK 特殊行为
- 有明确来源的“魔法数字”

避免在代码末尾堆叠很长的尾随注释。复杂说明应放在相关代码块之前。

统一使用以下标记：

```text
# TODO(负责人或 Issue, YYYY-MM-DD): 尚未实现的工作，以及暂缓原因
# FIXME(负责人或 Issue, YYYY-MM-DD): 已知缺陷、影响范围和修复方向
# NOTE: 容易误解但不是缺陷的重要背景
# PERF: 性能相关取舍，以及牺牲了什么来换取什么
```

要求：

- 不允许为了掩盖当前任务未完成而留下 TODO 或 FIXME
- 任务确实允许延期时，必须填写可追踪的负责人、Issue 或日期，并在交付说明中明确指出
- 禁止保留整段注释掉的旧实现；历史代码通过 Git 追溯
- NOTE 和 PERF 必须提供额外信息，不能只是给普通注释增加标签

## 16.7 语言与术语

- 注释和 Docstring 以简体中文为主
- Embedding、Chunk、Rerank、Retriever、Prompt Injection、Hallucination 等专业术语保留英文
- 专业术语首次出现且不易理解时，可使用“中文解释（English Term）”，后续统一使用英文
- 变量、函数、类和协议字段仍使用英文，不为了中文注释修改代码命名
- 错误信息、外部字段和日志内容应使用其准确名称，不进行会造成歧义的强行翻译
- 注释行长度遵循项目格式与 Lint 限制，避免横向滚动才能读完

## 16.8 Good 与 Bad 示例

Bad：

```python
# 计算相似度
score = cosine_similarity(query_vector, document_vector)
```

这段注释只重复代码已经表达的动作。

Good：

```python
# Collection 使用 COSINE 距离，因此查询向量必须沿用建库时的同一 Embedding 模型。
# 如果查询和文档来自不同向量空间，数值仍可计算，但分数不再具有语义可比性。
score = cosine_similarity(query_vector, document_vector)
```

Bad：

```python
# 如果没有结果就返回
if not results:
    return []
```

Good：

```python
# 没有检索证据时直接返回，不调用 LLM 使用自身知识补全答案。
# 这是 RAG 的无证据降级边界，用于降低无法追溯来源的 Hallucination。
if not results:
    return []
```

## 16.9 注释维护与 AI Agent 检查清单

注释与代码具有同等维护责任。代码行为改变时，必须同步检查：

1. 模块级 Docstring 的职责、设计背景和限制是否仍然正确
2. 类与函数 Docstring 的参数、返回值、异常和 Side Effect 是否仍然正确
3. 函数内部步骤、顺序、状态变化和失败说明是否仍然正确
4. 超参数、模型名称、维度、Batch、Timeout 等约束是否仍然正确
5. Good/Bad、NOTE、TODO、FIXME 和 ADR 引用是否已经过期

AI Agent 新增或修改代码时必须：

1. 默认按照本节要求编写详尽注释，不因代码量大而省略
2. 主动检查修改附近的旧注释，发现不一致时在本次最小范围内同步修正
3. 对复杂函数同时提供 Docstring 和有空行分隔的块级注释
4. 对技术选择和超参数给出有事实依据的原因，不编造限制、实验结果或历史事故
5. Review Diff 时把注释作为正式实现的一部分检查

以下情况视为任务未完成：

```text
核心函数只有 Docstring，函数体内没有阶段说明
复杂代码连续堆叠，缺少空行和视觉分段
注释与实现不一致
注释只逐行翻译代码
关键参数没有来源或取舍说明
为了显得专业而编造设计背景、Benchmark 或踩坑经历
```

最终目标：

> **让读者既能从代码确认实现细节，也能从注释理解 RAG 原理、业务流程、技术背景和关键取舍。**

---

# 17. 类型安全

Python 生产代码应尽量使用类型标注。

重要函数应声明：

- 参数类型
- 返回类型

尽量避免随意使用：

```python
Any
```

API 边界使用：

```text
Pydantic
```

配置使用：

```text
Pydantic Settings
```

业务概念使用领域模型。

如果某个字典已经具有稳定业务含义，不要长期使用：

```python
dict[str, Any]
```

在整个系统中传递。

应该考虑定义明确模型。

---

# 18. 错误处理

禁止静默吞掉异常。

禁止：

```python
try:
    ...
except Exception:
    pass
```

错误应该：

1. 被明确处理
2. 转换为业务异常
3. 或继续抛给能够处理它的上层

可以在确有价值时定义业务异常，例如：

```text
DocumentNotFoundError
UnsupportedDocumentTypeError
ParserNotFoundError
DocumentParseError
EmbeddingError
VectorStoreError
```

但不要创建几十个没有实际区别的 Exception Class。

---

# 19. 外部服务默认不可靠

以下依赖都应认为可能失败：

```text
PostgreSQL
MinIO
Milvus
Embedding Model
LLM API
OCR Service
Parser Service
```

在合理情况下考虑：

- Timeout
- 有界重试
- 错误信息
- 资源释放
- 幂等
- 状态记录

禁止：

```text
无限重试
```

Retry 只应该用于：

> 很可能是临时故障的问题。

例如：

```text
Network Timeout
Temporary Service Unavailable
```

而不是：

```text
参数错误
文件损坏
不支持格式
```

---

# 20. 状态一致性

绝不能让未完成处理的文档被当成可用文档。

文档状态：

```text
PENDING
PARSING
CHUNKING
EMBEDDING
INDEXING
READY
FAILED
```

只有全部必要步骤成功：

```text
Parse
Chunk
Embedding
Index
```

之后才能：

```text
READY
```

失败时必须保留可理解状态。

修改涉及：

```text
PostgreSQL
MinIO
Milvus
```

多个资源时，要考虑失败后的数据一致性。

当前阶段不要求实现复杂分布式事务。

优先：

> 明确失败状态 + 补偿操作

而不是假装多个系统之间存在原子事务。

---

# 21. 幂等性

对于可能被重试的操作，应尽量保证幂等。

重点包括：

```text
Document Indexing
Vector Upsert
Document Delete
Reindex
Background Job
```

尽量使用稳定 ID。

避免一次任务重试后产生：

```text
重复 Chunk
重复 Vector
重复 Document
```

---

# 22. 数据库规范

使用：

```text
SQLAlchemy
```

进行数据库访问。

数据库结构变化使用：

```text
Alembic
```

创建 Migration。

不要依赖应用启动时自动修改生产数据库结构。

避免：

```text
循环中不断查询数据库
```

造成明显 N+1 Query。

需要一致性的操作使用数据库 Transaction。

Repository 应表达：

> 业务语义

而不是让各种模块到处直接拼 SQL。

---

# 23. API 规范

API 应保持：

- 清晰
- 稳定
- 可预测
- 结构统一

使用：

```text
明确 Resource URL
正确 HTTP Status Code
结构化 Request
结构化 Response
统一 Error Response
```

不要把内部 Stack Trace 返回给客户端。

所有外部输入必须验证。

Controller / Route 应尽量薄。

推荐：

```text
Validate Request
      ↓
Application Service
      ↓
Response
```

不要把：

```text
数据库
MinIO
Milvus
Embedding
Prompt
LLM
```

全部塞进 FastAPI Route 中。

---

# 24. 文件上传安全

所有用户上传文件都必须认为：

> 不可信。

至少校验：

- 扩展名
- 文件大小
- MIME Type（适用时）
- 文件名

不要直接使用用户文件名构造本地文件系统路径。

避免：

```text
Path Traversal
```

不要执行用户上传内容。

原始文件应使用系统生成的 Object Key 保存。

---

# 25. 安全规范

禁止提交：

```text
API Key
Password
Access Token
Private Credential
生产密钥
```

配置使用环境变量。

`.env.example` 中不得包含真实 Secret。

不要把 Secret 写入日志。

不要在异常信息中泄漏：

```text
Token
Password
Credential
```

RAG 检索得到的文档内容应该视为：

> **不可信输入。**

文档中的文字不能覆盖：

```text
System Prompt
安全规则
应用指令
```

---

# 26. 日志规范

日志必须帮助定位问题。

优先携带：

```text
request_id
knowledge_base_id
document_id
chunk_id
```

推荐结构化日志。

默认不要记录：

- 完整文档正文
- 完整 Prompt
- Embedding Vector
- API Key
- Secret
- 大量 Chunk 内容

避免循环里产生大量无意义日志。

重点记录：

```text
任务开始
关键状态变化
任务完成
任务失败
外部服务错误
```

---

# 27. Async 使用原则

只有在真正适合 I/O 场景时使用：

```python
async / await
```

不要把整个 Python 项目机械改成：

```python
async def
```

CPU 密集任务不能长期阻塞 Event Loop。

未来例如：

```text
OCR
PDF Parsing
Embedding
大型文档处理
```

可能需要：

```text
Worker Process
独立服务
Task Queue
```

但只有当前版本需要时才引入。

---

# 28. 性能原则

优先级：

```text
正确性
可读性
可维护性
```

高于：

```text
微优化
```

不要在没有性能数据之前进行复杂优化。

但必须避免明显问题，例如：

- N+1 Query
- 每个 Chunk 单独请求一次 Embedding
- 每次请求重新加载模型
- 重复下载模型
- 大文件无脑全部读入内存
- 大量重复复制文本
- 对同一数据重复计算

优化之前：

> 先测量。

---

# 29. 高可用与健壮性原则

当前项目不需要为了“企业级”三个字就提前搭建完整高可用集群。

但代码应该天然具备未来高可用演进基础。

应做到：

- 故障行为可预测
- 不依赖隐式全局可变状态
- 持久业务状态不能只放内存
- 应用重启后可以恢复
- 外部服务通过清晰边界访问
- 失败后状态可追踪
- 资源可以重新初始化

当前没有明确需求时，不要主动引入：

```text
Kubernetes
Service Mesh
Distributed Lock
Cluster Scheduler
复杂分布式一致性协议
```

设计可以为未来保留空间，但不要提前实现。

---

# 30. RAG 专项开发规范

RAG 各阶段应该保持职责分离：

```text
Parsing
Chunking
Embedding
Indexing
Retrieval
Context Building
Generation
Citation
Evaluation
```

不要把整个流程写进一个超大函数。

每个 Retrieval Result 应尽可能保存：

```text
knowledge_base_id
document_id
chunk_id
source locator
retrieval score
```

保证结果可以追溯到原始来源。

Retrieval 必须能够：

> 不依赖 LLM 单独测试。

不要把：

```text
Retrieval
+
Generation
```

绑死为无法拆开的黑盒。

---

# 31. LLM 使用原则

不要把本可以由普通程序稳定完成的逻辑交给 LLM。

适合 LLM：

```text
Generation
Query Rewrite
复杂语义分类
语义评估
```

不适合 LLM：

```text
文件后缀判断
简单 Validation
基础数据转换
数据库过滤
确定性 Routing
```

LLM 输出同样属于：

> 不可信外部输出。

如果需要结构化结果，必须验证。

---

# 32. LangChain 使用原则

LangChain 可以使用。

但它应该是：

> Tool / Adapter

而不是：

> UltimateRAG 的领域核心。

禁止让 UltimateRAG 核心模型依赖：

```text
LangChain Document
LangChain Retriever
LangChain VectorStore
```

等具体实现。

需要时可以建立：

```text
UltimateRAG
     ↓
LangChain Adapter
     ↓
LangChain
```

而不是：

```text
UltimateRAG = LangChain
```

---

# 33. LangGraph 使用原则

不要因为这是 RAG 项目就默认使用 LangGraph。

简单确定性流程：

```text
Parse
→ Chunk
→ Embed
→ Index
```

或者：

```text
Query
→ Retrieve
→ Generate
```

优先使用普通 Python Service 编排。

当真正出现以下需求时再考虑 LangGraph：

```text
Conditional Routing
Loop
Stateful Workflow
Agent Decision
Tool Calling
Self Correction
Human In The Loop
```

框架应该解决真实复杂度。

不要人为制造复杂度来证明框架有用。

---

# 34. Dependency 依赖规范

新增依赖之前必须判断：

1. 项目是否已经有类似能力
2. Python 标准库是否足够
3. 这个依赖是否成熟
4. 是否持续维护
5. 是否增加大量传递依赖
6. 是否真的值得

不要为了一个简单 Helper 引入整个第三方库。

Python 依赖统一使用：

```text
uv
```

并维护：

```text
uv.lock
```

不要同时维护多套冲突的依赖方式。

---

# 35. 测试要求

每次有意义的修改，都应该进行相应验证。

重要模块必须具有 Unit Test。

适合时建立 Integration Test。

至少考虑：

```text
正常输入
非法输入
空输入
边界情况
失败路径
```

不要为了 Coverage 百分比写没有价值的测试。

测试应该验证：

> 行为。

不要 Mock 到最后测试已经无法证明真实功能。

---

# 36. Bug 修复规范

修 Bug 时：

1. 理解根因
2. 不要只修表面现象
3. 尽可能增加 Regression Test
4. 控制修改范围
5. 不要顺便重构无关模块

如果一个 Bug 只需要改 10 行代码：

不要顺便重构 20 个文件。

除非现有架构确实无法安全修复。

---

# 37. 重构规范

重构必须有明确原因。

合理原因包括：

- 重复代码导致维护风险
- 逻辑难理解
- 模块职责混乱
- 外部依赖需要隔离
- 测试困难
- 架构边界已经被破坏

不要因为：

> “另一种写法更优雅”

就重构大量稳定代码。

不要同时：

```text
做 Feature
+
大规模 Refactor
```

除非确实必要。

---

# 38. 前端规范

前端优先：

- 清晰
- 简单
- 可维护
- 易使用

遵循：

```text
React
Next.js
TypeScript
```

常规最佳实践。

不要过早创建复杂：

```text
Component Framework
Design System
Abstract Form System
通用 CRUD Framework
```

避免一个巨大 Component 同时承担：

```text
API Request
业务逻辑
State
数据转换
UI Render
格式化
```

页面至少处理：

```text
Loading
Empty
Error
Success
```

状态。

在核心 RAG 能力稳定之前，不要把大量开发时间投入装饰性 UI。

---

# 39. 产品体验原则

系统状态应该对用户可见。

例如文档处理：

```text
Pending
Parsing
Chunking
Embedding
Indexing
Ready
Failed
```

错误信息应尽可能可操作。

避免只显示：

```text
Something went wrong
```

如果系统明确知道：

```text
Markdown 解析失败
Milvus 无法连接
Embedding 服务超时
```

应提供更准确的信息。

Retrieval Playground 可以展示：

```text
Document
Section
Score
Chunk Content
```

方便调试。

普通用户界面则避免展示过多内部技术细节。

---

# 40. AI Agent 修改纪律

AI Agent 每次开始任务时必须遵循：

## 开发前

- 阅读相关代码
- 理解现有实现
- 搜索已有模式
- 明确任务范围
- 选择最小合理修改方案

---

## 开发中

- 不偏离用户要求
- 不擅自扩 Scope
- 不随意改架构
- 不增加无关 Feature
- 不顺手清理整个项目
- 保持 Diff 易审查
- 复用既有模式

---

## 开发后

必须：

- Review Diff
- 运行相关测试
- 运行 Lint
- 运行 Type Check（如果项目已配置）
- 检查 Import
- 检查 Error Path
- 检查配置变化
- 必要时更新文档

不能：

> 代码写完就直接宣布完成。

---

# 41. 禁止修改无关代码

AI Agent 特别容易扩大修改范围。

禁止无理由：

- 重命名无关变量
- 改整个项目格式
- 调整无关 Import
- 重写相邻模块
- 替换已有依赖
- 修改与任务无关 API
- 清理用户没有要求处理的代码

优先：

> 小而清晰的 Diff。

而不是：

> 一次提交改几十个无关文件。

---

# 42. 保持向后兼容

除非任务明确允许 Breaking Change，否则：

不要随意破坏：

```text
已有 API
已有数据结构
已有配置
已有行为
已有调用方式
```

如果确实必须 Breaking Change：

1. 明确说明
2. 修改所有受影响调用方
3. 更新测试
4. 更新文档
5. 必要时提供迁移说明

---

# 43. 文档规范

当修改涉及：

```text
架构
API
配置
部署
行为
```

时，应同步更新相关文档。

不要把所有项目知识都堆进：

```text
AGENTS.md
```

详细知识应该进入：

```text
docs/
```

重要技术决策使用：

```text
ADR
```

例如：

```text
docs/adr/ADR-008-why-minio.md
```

ADR 建议包含：

```text
Context
背景

Decision
决策

Alternatives
备选方案

Consequences
影响
```

---

# 44. Git 与代码提交规范

本节规范从工作区检查、暂存、Commit Message 到提交后验证的完整流程。

提交的目标不是把当前文件“保存进 Git”，而是形成：

> **范围清晰、原因明确、可以独立审查、可以安全回滚的项目历史。**

## 44.1 Git 安全边界

除非用户明确要求并确认影响范围，否则禁止：

```text
git reset --hard
git push --force
git push --force-with-lease
git clean -fd
git checkout -- <file>
git restore <file>
删除或覆盖用户未提交修改
修改已经发布的 Git 历史
```

AI Agent 还必须遵守：

1. 用户没有明确要求“提交”时，不得自行创建 Commit
2. 用户要求 Commit 不等于要求 Push；没有明确授权时不得推送远端
3. 不得擅自 Amend、Rebase、Cherry-pick 或合并分支
4. 不得使用 `--no-verify` 绕过 Git Hook
5. 工作区不干净时，必须区分用户原有修改与本次任务修改
6. 不得为了获得干净工作区而删除、覆盖或隐藏无法确认归属的修改

## 44.2 提交粒度

每个 Commit 应只表达一个明确的修改原因。

推荐：

```text
一个 Bug Fix + 对应 Regression Test
一个 Feature + 对应测试与必要文档
一次有明确目的的 Refactor + 行为不变验证
一次数据库结构变化 + 对应 Alembic Migration
一次依赖调整 + pyproject.toml 与 uv.lock
```

不推荐把以下无关内容混在一个 Commit：

```text
Feature + 无关代码格式化
Bug Fix + 大规模重构
业务代码 + 临时调试文件
后端接口 + 无关前端样式调整
依赖升级 + 无关功能开发
多个没有共同原因的 Bug Fix
```

判断标准：

> 如果这个 Commit 被单独 Revert，是否只撤销一个完整且明确的行为变化？

同一行为所需的生产代码、测试、Migration、配置示例和文档应放在同一个 Commit 中，避免产生“代码已提交但测试或迁移留到下一个 Commit”的中间状态。

## 44.3 暂存规范

提交前先检查整个工作区：

```bash
git status --short
git diff
```

工作区存在其他修改时，应使用明确路径暂存本次文件：

```bash
git add src/ultimate_rag/application/services.py
git add tests/unit/test_ingestion_service.py
```

不要在无法确认工作区全部修改归属时直接使用：

```bash
git add .
git add -A
```

暂存后必须再次检查真正会进入 Commit 的内容：

```bash
git diff --cached --stat
git diff --cached
```

确认：

- 暂存内容只属于当前任务
- 没有遗漏配套测试、Migration、配置示例或文档
- 没有把用户的其他未完成工作一起提交
- 没有 Secret、Cache、日志、模型文件、构建产物或本地环境垃圾
- 没有临时 `print()`、调试断点、无原因的 TODO/FIXME 或注释掉的旧代码
- Rename、Delete 和新增文件都是预期行为

## 44.4 Commit Message 格式

UltimateRAG 使用 Conventional Commits 风格：

```text
<type>(<scope>): <summary>

<body>

<footer>
```

其中只有第一行是必需的。简单修改可以省略 Body 和 Footer，复杂修改必须解释背景与关键取舍。

### Type

允许使用：

| Type | 使用场景 |
|---|---|
| `feat` | 增加用户可见的新能力 |
| `fix` | 修复错误行为或数据问题 |
| `refactor` | 不改变外部行为的代码结构调整 |
| `perf` | 有数据依据的性能改进 |
| `test` | 只增加或调整测试 |
| `docs` | 只修改文档或代码注释规范 |
| `build` | 构建系统、镜像、打包配置变化 |
| `ci` | CI/CD 工作流变化 |
| `chore` | 不属于以上类型的维护工作 |
| `style` | 只修改格式，不改变语义 |
| `revert` | 撤销一个已有 Commit |

禁止使用含义模糊的 Type，例如：

```text
update
change
misc
other
```

### Scope

Scope 应表达主要受影响的业务或架构模块，保持简短、稳定。

推荐 Scope：

```text
domain
ingestion
parser
chunker
embedding
vectorstore
retrieval
generation
evaluation
api
db
storage
web
config
deps
docs
agents
```

不确定 Scope 时可以省略，不要为了满足格式创造含义模糊的 Scope。

### Summary

Summary 必须：

1. 使用简体中文描述完成后的结果
2. 优先说明用户行为、业务约束或修复效果
3. 使用动宾结构，不写过程流水账
4. 不以句号结尾
5. 建议控制在 72 个字符以内
6. 避免“更新代码”“修复问题”“调整文件”等无法独立理解的表达

推荐：

```text
feat(ingestion): 支持 Markdown 文档同步摄取
fix(ingestion): 仅在向量索引成功后标记文档可用
fix(chunker): 避免超长段落重复叠加 overlap
refactor(api): 将 HTTP 入口迁移到独立应用包
docs(agents): 完善学习型代码注释规范
test(retrieval): 覆盖知识库隔离的检索行为
build(deps): 升级 OpenAI SDK 并同步锁文件
```

不推荐：

```text
update files
fix code
修改了一些问题
feat: 完成开发
chore: changes
```

## 44.5 Commit Body

以下情况必须编写 Body：

- 修改跨越多个模块或存储系统
- 涉及安全、状态一致性、幂等或事务边界
- 修改数据库 Schema、API 契约或配置行为
- 引入、移除或升级重要依赖
- 选择了一种存在明显 Trade-off 的技术方案
- 测试未运行、无法运行或存在已知失败
- 单看 Summary 无法理解为什么需要这个修改

Body 应重点说明：

```text
问题或需求背景
为什么采用当前实现
重要行为与边界
被放弃的主要方案及原因（确有评估时）
风险、兼容性和迁移要求
验证方式或未验证原因
```

推荐示例：

```text
fix(ingestion): 仅在向量索引成功后标记文档可用

文档此前可能在 Milvus 写入完成前进入 READY，导致检索到不完整索引。
现在按 PARSING、CHUNKING、EMBEDDING、INDEXING 顺序记录状态，并将
READY 保留为 PostgreSQL Chunk 和 Milvus 向量均持久化后的最终状态。

失败时保留 MinIO 原文件和 FAILED 文档事实，便于排查与后续重建。
```

Body 不应只是重复文件列表或 Summary。

## 44.6 Footer、Issue 与 Breaking Change

关联 Issue 时使用：

```text
Refs: #123
Closes: #123
```

只有 Commit 合并后应自动关闭 Issue 时才使用 `Closes`。

存在 Breaking Change 时，必须：

1. 在 Type 或 Scope 后添加 `!`
2. 在 Footer 中使用 `BREAKING CHANGE:` 说明影响和迁移方法
3. 同步更新所有调用方、测试和迁移文档

示例：

```text
feat(api)!: 统一文档上传响应结构

BREAKING CHANGE: POST /documents 不再返回旧版 data 包装层。
客户端需要直接读取 document_id、status 和 filename 字段。
```

除非用户明确允许 Breaking Change，否则不得仅通过 Commit Message 宣告破坏兼容性。

## 44.7 提交前验证

默认至少执行：

```bash
git diff --check
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

根据修改范围追加：

```text
API 变化：验证 Request、Response、Status Code 和 Error Mapping
数据库变化：检查 Alembic Upgrade/Downgrade 和数据兼容性
依赖变化：确认 pyproject.toml 与 uv.lock 同步
配置变化：检查 .env.example、Compose 和部署文档
前端变化：运行前端 Lint、Type Check、Test 和 Build
外部适配器变化：运行对应 Integration Test 或明确说明未运行原因
```

如果某项验证因环境限制无法运行，必须在交付说明和必要的 Commit Body 中明确：

```text
没有运行什么
为什么无法运行
已经完成哪些替代验证
仍然存在什么风险
```

禁止在测试失败时写“全部验证通过”。

## 44.8 提交内容安全检查

禁止提交：

```text
.env
真实 API Key、Password、Token、Cookie、Credential
生产连接字符串
完整 Prompt 或用户文档正文的调试日志
Embedding Vector 或模型权重
数据库 Dump
IDE 本地配置
Python Cache
测试临时输出
构建产物
与任务无关的大文件
```

`.env.example` 只能包含安全占位值，不能包含可用凭据。

新增文件或依赖后，应检查：

- `.gitignore` 是否覆盖本地生成物
- 文件大小是否合理
- License 是否允许使用
- 是否意外引入大量传递依赖
- 日志、异常和 Fixture 是否包含敏感业务内容

## 44.9 提交后的检查

Commit 创建后必须检查：

```bash
git status --short
git show --stat --oneline HEAD
```

确认：

- Commit 已包含预期文件
- 工作区剩余修改都是明确未提交的其他工作
- Commit Message 与实际 Diff 一致
- 没有因为 Commit 而丢失用户修改

如果发现提交内容错误，不得擅自 Amend 或 Reset。AI Agent 应先说明问题，并根据用户授权选择追加修复 Commit 或修改历史。

## 44.10 AI Agent 提交说明

AI Agent 完成 Commit 后，应向用户报告：

```text
Commit Hash
Commit Message
包含的主要修改
验证结果
工作区是否仍有未提交修改
是否尚未 Push
```

不要只回复“已提交”。

---

# 45. Definition of Done

一个任务只有满足以下条件才算完成：

- 用户要求的功能已经实现
- 符合现有架构
- 没有明显架构越界
- 核心异常已经处理
- 相关测试通过
- Lint 通过
- Type Check 通过（如果已配置）
- 没有无关改动
- 必要文档已经更新
- 没有 Secret
- 没有 Debug 临时代码

不要留下：

```python
print(...)
pass
TODO
FIXME
```

同时又声称功能已经完成。

如果确实需要保留 TODO，应明确说明原因。

---

# 46. 技术决策优先级

发生技术取舍时，优先级：

```text
正确性
   ↓
清晰度
   ↓
可维护性
   ↓
健壮性
   ↓
可测试性
   ↓
性能
   ↓
抽象优雅程度
```

但：

```text
安全
数据完整性
```

在任何情况下都具有更高优先级。

---

# 47. AI 生成代码特别约束

AI Agent 必须避免以下典型问题。

## 47.1 不要一次生成大量无必要代码

如果 100 行可以解决，不要写 1000 行。

---

## 47.2 不要为了“完整性”补用户没要求的功能

例如用户要求：

```text
实现 Markdown Upload
```

不要自动补：

```text
用户系统
ACL
Kafka
OCR
GraphRAG
```

---

## 47.3 不要创建伪企业级架构

不要通过堆：

```text
Factory
DDD
CQRS
Event Sourcing
Microservice
Kafka
Kubernetes
```

制造“企业级”假象。

企业级首先意味着：

```text
正确
稳定
清晰
可维护
可观察
可恢复
```

而不是技术名词多。

---

## 47.4 不要过度拆文件

不要一个简单 Feature 创建：

```text
interface.py
base.py
factory.py
manager.py
provider.py
resolver.py
handler.py
service.py
impl.py
```

除非每层确实承担明确职责。

---

## 47.5 不要重复造相同逻辑

如果已有：

```text
Error Handling
Logging
Repository
Configuration
```

模式，应复用。

不要每次重新创造一套。

---

# 48. UltimateRAG 的长期技术原则

UltimateRAG 最终希望做到：

```text
Parser 可插拔
Chunk Strategy 可替换
Embedding 可替换
Vector DB 可替换
Retriever 可组合
Reranker 可插拔
LLM 可替换
Workflow 可演进
```

但注意：

> “可替换”不等于“现在就必须实现所有实现”。

V1.0 可以只有：

```text
MarkdownParser
BGE-M3
Milvus
OpenAI-Compatible LLM
```

只需要架构边界合理。

---

# 49. 最重要的开发原则

UltimateRAG 的代码应该让人感觉：

> 这是经验丰富的工程师写出来的系统。

而不是：

> 这是 AI 为了展示能力一次生成的一大坨代码。

优先：

> 职责清晰、行为明确、容易阅读的普通代码。

不要追求：

> 看起来高级但难理解的聪明代码。

始终遵守：

> **简单优于复杂。**

> **明确优于隐式。**

> **可维护优于炫技。**

> **解决当前问题优于预测未来。**

> **每一个抽象都必须证明自己的价值。**

> **每一个依赖都必须证明自己的成本值得。**

> **每一个模块都应该有清晰职责。**

> **每一个错误都应该能够被理解和定位。**

> **每一个重要行为都应该能够测试。**

最终目标是：

> **使用能够正确解决当前问题的最简单架构，同时保留 UltimateRAG 已经明确需要的长期扩展能力。**
