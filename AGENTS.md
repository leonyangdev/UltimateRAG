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

# 16. 注释规范

注释主要解释：

> **为什么这么做。**

不要重复代码本身已经表达清楚的内容。

不推荐：

```python
# 设置状态为 READY
document.status = DocumentStatus.READY
```

推荐：

```python
# 只有 Milvus 索引成功后才允许进入 READY，
# 防止未完成索引的文档被检索。
document.status = DocumentStatus.READY
```

不要让 AI 自动生成大量没有价值的注释。

优先通过：

```text
清晰命名
清晰结构
清晰函数
```

让代码自解释。

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

# 44. Git 操作规范

除非用户明确要求，否则不要进行危险 Git 操作。

禁止擅自：

```text
reset --hard
force push
删除用户修改
覆盖未提交代码
```

任务结束前建议检查：

```bash
git diff
git status
```

确认：

- 没有无关文件
- 没有 Secret
- 没有 Cache
- 没有模型文件
- 没有本地环境垃圾文件

Commit Message 应说明：

> 为什么做这个修改。

而不是只写：

```text
update files
fix code
```

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
