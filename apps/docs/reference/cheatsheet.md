# 目录速查表

一页看清项目结构、术语、状态值——适合读到一半忘了「某文件在哪 / 某状态什么意思」时回来查。

## 1. 后端目录

```text
src/ultimate_rag/
├── domain/                 # 领域层（项目词汇表）
│   ├── models.py           #   Document / Block / Chunk / RetrievalResult / Citation ...
│   ├── ports.py            #   8 个 Protocol 端口
│   └── exceptions.py       #   业务异常体系
├── application/            # 应用层（业务编排）
│   ├── services.py         #   Ingestion / Processing / Retrieval / RAG / Lifecycle
│   └── context.py          #   ContextBuilder 拼上下文
├── parsers/                # 7 种格式 → ParsedDocument
│   ├── registry.py         #   按 supports() 选 Parser
│   ├── _shared.py          #   输入安全、表格转 Markdown
│   ├── markdown.py / html.py / pdf.py / office.py / image.py
├── chunkers/markdown.py    # StructureAwareChunker 切块
├── embeddings/bailian.py   # BailianEmbedder 向量化
├── vectorstores/milvus.py  # MilvusVectorStore
├── generation/bailian.py   # BailianLLMClient 生成
├── ocr/  vision/           # 百炼 OCR 与视觉理解适配器
├── infrastructure/
│   ├── database/models.py  #   4 张 SQLAlchemy 表
│   ├── database/repository.py  # 业务语义数据访问 + 任务领取/租约
│   └── storage/minio.py    #   MinIO 原始文件
├── runtime.py              # Composition Root（依赖装配）
├── worker.py               # 独立 Worker 进程
└── config.py               # Pydantic Settings
```

## 2. 前端与 API 目录

```text
apps/api/                   # FastAPI（Interface 层）
├── app.py                  #   应用、Lifespan、异常映射
├── routes.py               #   全部 HTTP 路由
├── schemas.py              #   Pydantic 请求/响应
└── container.py            #   进程级依赖容器

apps/web/                   # Next.js 前端
├── app/lib.ts              #   API 封装 + 类型 + API 地址解析
├── app/chat/page.tsx       #   聊天页
├── app/knowledge-bases/    #   知识库列表 / 详情
└── components/             #   聊天消息、检索证据、UI 组件
```

## 3. 术语表

| 术语 | 含义 |
|---|---|
| Ingestion | 入库：把文档解析、切块、向量化、索引的过程 |
| ParsedDocument | Parser 输出的统一文档结构（Block 序列） |
| Block | 解析后的最小结构单元（标题/正文/表格/代码/图片） |
| Chunk | 用于向量化的切块单元 |
| EmbeddedChunk | Chunk + 其向量 |
| RetrievalResult | 一次检索命中（含来源定位与相似度） |
| Citation | 答案引用的结构化来源 |
| SourceLocator | 跨格式原文位置（页码/区域/幻灯片） |
| Port（端口） | 领域层定义的抽象接口（Protocol） |
| Adapter（适配器） | 基础设施层对端口的实现 |
| Composition Root | 集中装配所有依赖的入口 |
| 派生索引 | Milvus 中可由事实数据重建的向量索引 |

## 4. 状态值速查

### DocumentStatus

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
                              └─ 失败 → FAILED
```

### IngestionJobStatus

```text
PENDING → RUNNING → SUCCEEDED
   │         │
   └──失败───┘→ FAILED（终态） 或 回 PENDING（有限重试）
```

## 5. HTTP 状态码

| 状态 | 含义 |
|---|---|
| 400 | 输入错误（文件超限/格式不支持/参数不合法） |
| 404 | 知识库/文档不存在 |
| 409 | 文档正在后台处理 |
| 202 | 上传已入队（非完成） |
| 204 | 删除成功 |
| 502 | 外部处理故障 |

## 6. 端口 → 实现对照

| 端口 | 当前实现 |
|---|---|
| `DocumentParser` | MarkdownParser / PDFParser / WordParser / ExcelParser / PowerPointParser / HtmlParser / ImageOCRParser |
| `Chunker` | StructureAwareChunker |
| `Embedder` | BailianEmbedder |
| `VectorStore` | MilvusVectorStore |
| `ObjectStorage` | MinioObjectStorage |
| `LLMClient` | BailianLLMClient |
| `OCRClient` | BailianOCRClient |
| `VisionClient` | BailianVisionClient |

## 7. 关键文件一句话索引

| 想知道什么 | 看哪个文件 |
|---|---|
| 文档有哪些字段 | `domain/models.py` |
| 系统支持哪些异常 | `domain/exceptions.py` |
| 依赖怎么拼起来的 | `runtime.py` |
| 上传后发生了什么 | `application/services.py` → `submit` |
| 后台怎么处理文档 | `application/services.py` → `process` |
| 任务怎么领取/续租 | `infrastructure/database/repository.py` → `claim_ingestion_job` |
| Worker 循环 | `worker.py` → `IngestionWorker` |
| 一个问题怎么被回答 | `application/services.py` → `RAGService` |
| 流式协议 | `apps/api/routes.py` → `stream_chat` |
| 有哪些 API | `apps/api/routes.py` |
| 配置项 | `config.py` |
