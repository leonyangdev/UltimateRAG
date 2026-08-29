# 模块总览

这一页给你一张「模块地图」：每个模块在流水线的哪个位置、负责什么、依赖谁。之后的页面会逐个展开。

## 1. 模块地图（按数据流排列）

```text
                    ┌─────────────────────────────────────────────┐
                    │              Interface 层                    │
                    │   apps/api（FastAPI）+ apps/web（Next.js）    │
                    └───────────────┬─────────────────────────────┘
                                    │ HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Application 层                                 │
│  IngestionService（上传入队）                                     │
│  DocumentProcessingService（后台处理管线）                         │
│  RetrievalService（检索）                                         │
│  RAGService（问答生成）                                           │
│  DocumentLifecycleService（删除）                                  │
│  ContextBuilder（拼上下文）                                       │
└──────────┬───────────────────────────┬───────────────────────────┘
           │                           │
           ▼                           ▼
┌─────────────────────────┐   ┌───────────────────────────────────┐
│       Domain 层          │   │   Infrastructure / Adapter 层      │
│  models.py（领域模型）    │   │                                   │
│  ports.py（端口契约）     │◄──┤  parsers/（7 种解析器）            │
│  exceptions.py（异常）    │   │  chunkers/（StructureAwareChunker）│
└─────────────────────────┘   │  embeddings/（BailianEmbedder）     │
                              │  vectorstores/（MilvusVectorStore） │
                              │  generation/（BailianLLMClient）    │
                              │  ocr/ + vision/（百炼 OCR/视觉）     │
                              │  infrastructure/（PostgreSQL+MinIO）│
                              │  worker.py（后台 Worker 进程）       │
                              └───────────────────────────────────┘
```

## 2. 模块职责速查

| 模块 | 代码位置 | 一句话职责 | 详细页 |
|---|---|---|---|
| **Domain** | `domain/` | 领域模型 + 端口契约 + 业务异常 | [Domain](/modules/domain) |
| **Application** | `application/` | 显式业务工作流编排 | [Application](/modules/application) |
| **Parsers** | `parsers/` | 各种格式 → 统一文档模型 | [Parsers](/modules/parsers) |
| **Chunker** | `chunkers/` | 统一模型 → 可检索 Chunk | [Chunker](/modules/chunker) |
| **Embedding** | `embeddings/` | 文本 → 向量 | [Embedding](/modules/embeddings) |
| **VectorStore** | `vectorstores/` | 向量写入与检索 | [VectorStore](/modules/vectorstore) |
| **Generation** | `generation/` | LLM 生成答案 | [Generation](/modules/generation) |
| **Worker** | `worker.py` | 后台异步处理任务 | [Worker](/modules/worker) |
| **Infrastructure** | `infrastructure/` | PostgreSQL + MinIO 实现 | [Infrastructure](/modules/infrastructure) |
| **API & Web** | `apps/api`、`apps/web` | HTTP 接口与前端 | [API 与 Web](/modules/api-web) |

## 3. 模块之间的关键调用链

### 入库调用链

```text
apps/api/routes.py（上传）
  → IngestionService.submit
      → Repository.get_knowledge_base（校验存在）
      → ParserRegistry.resolve（校验格式有 Parser）
      → MinioObjectStorage.put（存原文件）
      → Repository.create_document_with_job（建文档+任务，同事务）
      → 返回 202 / PENDING

worker.py（后台）
  → Repository.claim_ingestion_job（领任务）
  → DocumentProcessingService.process
      → ParserRegistry.resolve → Parser.parse
      → StructureAwareChunker.split
      → BailianEmbedder.embed_documents
      → Repository.replace_chunks + MilvusVectorStore.upsert
      → Repository.update_document_status(READY)
```

### 问答调用链

```text
apps/api/routes.py（/chat 或 /chat/stream）
  → RAGService.answer / stream_answer
      → RetrievalService.search
          → BailianEmbedder.embed_query
          → MilvusVectorStore.search
          → Repository.list_ready_document_ids（二次过滤）
      → ContextBuilder.build（拼上下文）
      → BailianLLMClient.generate / stream
      → 返回 answer + citations + retrieval_results
```

## 4. 依赖装配（谁创建了谁）

- `runtime.py`（`create_processing_runtime`）是 **Composition Root**：创建数据库、MinIO、Milvus、百炼客户端、Parser 注册表、Chunker，并装配 `IngestionService` 和 `DocumentProcessingService`。
- `apps/api/app.py` 的 `lifespan` 调用 `create_processing_runtime`，再额外装配 `RetrievalService`、`RAGService`、`DocumentLifecycleService`，全部放进 `app.state.container`。
- 路由层通过 `container(request)` 拿到这些服务，自己**不创建任何客户端**。

## 下一步

- 从最核心的领域层开始 → [Domain 领域层](/modules/domain)
- 或者从你感兴趣的具体模块开始，用上面的表格跳转
