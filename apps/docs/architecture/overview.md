# 整体架构与分层

## 1. 一句话全景

UltimateRAG 是一个 **Monorepo 前后端分离**的项目：

```text
浏览器（Next.js Web）
      │  HTTP / SSE
      ▼
FastAPI（apps/api）
      │  调用应用服务
      ▼
RAG 核心库（src/ultimate_rag）
      │  通过「端口」调用外部能力
      ▼
PostgreSQL（事实） · MinIO（原始文件） · Milvus（Dense/BM25 索引） · 百炼模型（Embedding/Rewrite/Rerank/LLM/OCR/Vision）
```

## 2. 分层架构

核心库采用 **Interface → Application → Domain ← Infrastructure** 的四层依赖方向：

```text
Interface（接口层）      apps/api：FastAPI 路由、请求/响应 Schema、依赖容器
     ↓ 调用
Application（应用层）    application/services.py + retrieval.py：显式业务工作流编排
     ↓ 依赖
Domain（领域层）         domain/：领域模型（dataclass）+ 端口（Protocol）
     ↑ 实现
Infrastructure（基础设施） infrastructure/、parsers/、embeddings/、vectorstores/ 等外部适配器
```

::: tip 依赖方向是关键
箭头代表依赖方向：**上层依赖下层，下层绝不反向依赖上层**。
Domain 是最底层，谁都不依赖；Infrastructure 实现 Domain 定义的端口。
:::

### 2.1 各层职责

| 层 | 目录 | 职责 | 典型内容 |
|---|---|---|---|
| **Interface** | `apps/api` | HTTP 边界：校验输入、映射响应、错误码 | `routes.py`、`schemas.py`、`container.py`、`app.py` |
| **Application** | `src/ultimate_rag/application` | 业务工作流：把端口串成业务步骤 | `IngestionService`、`DocumentProcessingService`、`RetrievalService`、`RAGService` |
| **Domain** | `src/ultimate_rag/domain` | 业务事实：模型 + 可替换能力的接口 | `models.py`（Document/Block/Chunk…）、`ports.py`（Protocol）、`exceptions.py` |
| **Infrastructure** | `src/ultimate_rag/infrastructure` 等 | 外部能力的具体实现 | PostgreSQL Repository、MinIO、Milvus、百炼、各种 Parser |

## 3. 两个进程，共用一套核心库

V2 引入的异步摄取在 V3 继续保留：**「上传」和「处理」分属两个进程**，但共用同一个 Composition Root：

```text
进程 1：FastAPI（apps/api）
   ├── 处理上传：校验 → 存 MinIO → 建 Document+Job → 返回 202
   └── 处理问答：过滤/改写 → Dense+BM25 → RRF/重排 → Small2Big → LLM

进程 2：Worker（src/ultimate_rag/worker.py）
   └── 后台领取任务：解析 → 切块 → 向量化 → 索引 → READY
```

- 两个进程通过 `src/ultimate_rag/runtime.py` 的 `create_processing_runtime()` 装配**同一套** Parser、Chunker、存储、向量库。
- 好处：API 校验「支持什么格式」，Worker 就「一定能解析什么格式」，两套进程不会配置不一致。

```text
runtime.py（Composition Root）
   ├── Repository（PostgreSQL）
   ├── MinioObjectStorage（MinIO）
   ├── ParserRegistry（7 种 Parser）
   ├── StructureAwareChunker
   ├── BailianEmbedder
   ├── MilvusVectorStore
   └── 注入 → IngestionService / DocumentProcessingService
```

## 4. 核心架构图（含数据流向）

```text
┌───────────────────────────┐
│        Next.js Web        │
└────────────┬──────────────┘
             │ HTTP / SSE
             ▼
┌───────────────────────────────────────────────┐
│              FastAPI (apps/api)               │
│                                               │
│  POST /documents ────► IngestionService       │
│                          │ 校验 + 存 MinIO    │
│                          ▼                    │
│                PostgreSQL: Document + Job     │
│                返回 202 / PENDING             │
│                                               │
│  POST /chat ───────────► RAGService           │
│                          ├─ RetrievalService  │
│                          ├─ ContextBuilder    │
│                          └─ LLMClient         │
│                          └─ 答案 + Citation   │
└────────────┬──────────────────┬───────────────┘
             │                  │
             ▼                  ▼
      PostgreSQL(事实)      Milvus(Dense + BM25)
             ▲
             │
┌────────────┴──────────────┐
│      IngestionWorker      │
│  (src/ultimate_rag/worker)│
│                          │
│ 领取 Job → Process：      │
│ Parse → Chunk/Asset → Embed │
│  → Index → READY         │
└────────────┬──────────────┘
             │
             ▼
      MinIO(原始文件)
```

## 5. 核心可替换能力（端口）

领域层定义了一系列 **Protocol（端口）**，由不同的基础设施实现。这是「可替换」的根基：

| 端口 | 职责 | V3 实现 |
|---|---|---|
| `DocumentParser` | 解析原始文件为统一模型 | Markdown / Word / Excel / PPT / HTML / PDF / Image |
| `Chunker` | 切块 | `StructureAwareChunker` |
| `Embedder` | 文本 → 向量 | `BailianEmbedder` |
| `VectorStore` | Dense/Sparse 派生索引写入与检索 | `MilvusVectorStore` |
| `QueryRewriter` | 生成一个保守查询变体 | `BailianQueryRewriter` |
| `Reranker` | 对有限候选做二阶段排序 | `BailianReranker` |
| `ObjectStorage` | 原始文件存储 | `MinioObjectStorage` |
| `LLMClient` | 文本生成 | `BailianLLMClient` |
| `OCRClient` | 图片 → 文字 | `BailianOCRClient` |
| `VisionClient` | 图片 → 语义描述 | `BailianVisionClient` |

> 想替换某个能力（例如换向量库），就实现对应的 Protocol，并在 `runtime.py` 里替换装配，其余代码不用改。

## 6. 为什么不用 LangChain / LangGraph

这是一个常被问到的问题，项目的答案是：

- **LangChain**：可以作为 Tool / Adapter，但**不能作为领域核心**。项目拥有自己的领域模型（`Document`、`Block`、`Chunk`…），不直接复用 LangChain 的 `Document` 数据结构。核心检索/生成不依赖 LangChain 的 Retriever/VectorStore。
- **LangGraph**：V3 虽有多个检索阶段，但仍是确定性流水线，用普通 Python Service 和并发召回已经足够。只有出现 Agent 决策、循环或人工介入等真实状态图时才考虑引入。

## 7. 关键设计原则速览

- Domain 不依赖 FastAPI、SQLAlchemy、Milvus、百炼 SDK、LangChain
- PostgreSQL 保存**业务事实**，Milvus 保存**可重建派生索引**
- 文档仅在全部处理步骤成功后进入 `READY`
- 上传的文件、LLM 输出都视为**不可信输入**
- 每个抽象都必须证明自己的价值，避免过度设计

各原则的展开见 [核心设计原则](/architecture/principles)。

## 下一步

- 想理解这些原则背后的取舍 → [核心设计原则](/architecture/principles)
- 想对照目录理解 → [项目目录结构](/architecture/directory)
- 想深入数据模型 → [核心领域模型](/architecture/data-model)
