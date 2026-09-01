# 项目目录结构

## 1. 顶层总览

```text
UltimateRAG/
├── apps/                        # 可独立运行的应用
│   ├── api/                     #   FastAPI 接口应用（Interface 层）
│   ├── web/                     #   Next.js 前端
│   └── docs/                    #   VitePress 文档网站（就是本网站）
├── src/ultimate_rag/            # RAG 核心库（Domain / Application / Infrastructure）
├── alembic/                     # 数据库迁移（唯一 Schema 变更入口）
├── docs/                        # 产品 / 架构 / ADR 文档（源文档）
├── scripts/                     # 可重复执行的发布验收脚本
├── tests/                       # 单元测试与固定文档
├── data/                        # 本地样例 + Git 忽略的 chunk_snapshots/ 诊断输出
├── pyproject.toml               # Python 依赖与工具配置（uv）
├── uv.lock                      # 锁文件
├── docker-compose.yml           # 一键启动全部服务
├── Dockerfile.api               # API 容器镜像
├── .env.example                 # 配置模板（不含真实密钥）
└── README.md
```

## 2. apps/ —— 三个应用

```text
apps/
├── api/                  # FastAPI 后端
│   ├── app.py            #   FastAPI 应用 + Lifespan 依赖装配 + 全局异常映射
│   ├── routes.py         #   HTTP 路由（薄，只做校验/映射）
│   ├── schemas.py        #   Pydantic 请求/响应模型
│   └── container.py      #   进程级依赖容器
│
├── web/                  # Next.js 前端
│   ├── app/              #   App Router 页面
│   │   ├── chat/         #     问答聊天页
│   │   └── knowledge-bases/  # 知识库列表 + 文档详情页
│   ├── components/       #   React 组件（rag-message、retrieval-evidence、ui/）
│   └── lib/              #   前端 API 封装、类型定义
│
└── docs/                 # VitePress 文档网站
    ├── .vitepress/config.mts
    ├── guide/            #   RAG 入门 + 项目概览
    ├── architecture/     #   架构与设计
    ├── modules/          #   模块详解
    ├── workflows/        #   核心流程
    └── reference/        #   API 参考
```

## 3. src/ultimate_rag/ —— RAG 核心库

这是项目的核心，按职责组织：

```text
src/ultimate_rag/
├── domain/                  # ★ 领域层：不依赖任何框架
│   ├── models.py            #   领域模型（dataclass）：Document/Block/Chunk/...
│   ├── ports.py             #   端口（Protocol）：Parser/Embedder/VectorStore/...
│   └── exceptions.py        #   业务异常
│
├── application/             # ★ 应用层：显式业务工作流
│   ├── services.py          #   Ingestion/Processing/RAG/Lifecycle
│   ├── retrieval.py         #   V3 高级检索显式流水线
│   └── context.py           #   ContextBuilder（拼上下文）
│
├── infrastructure/          # ★ 基础设施实现
│   ├── database/            #   SQLAlchemy 模型 + Repository
│   │   ├── models.py        #     表结构：knowledge_bases/documents/chunks/ingestion_jobs
│   │   └── repository.py    #     面向业务语义的数据访问
│   └── storage/
│       ├── minio.py            #   MinIO 对象存储适配器
│       └── chunk_snapshot.py   #   Embedding 前 UTF-8 JSON 原子快照
│
├── parsers/                 # 解析器（格式 → 统一模型）
│   ├── registry.py          #   ParserRegistry：按来源选择 Parser
│   ├── _shared.py           #   公共校验与工具
│   ├── _model_output.py     #   OCR/Vision Markdown 与伪表格清理
│   ├── markdown.py          #   MarkdownParser
│   ├── pdf.py               #   PDFParser（Docling + PDFium + 百炼）
│   ├── html.py              #   HtmlParser
│   ├── office.py            #   WordParser / ExcelParser / PowerPointParser
│   └── image.py             #   ImageOCRParser
│
├── chunkers/
│   └── markdown.py          #   StructureAwareChunker（结构+Token+类型切块）
│
├── embeddings/
│   └── bailian.py           #   BailianEmbedder（百炼 text-embedding-v4）
│
├── vectorstores/
│   └── milvus.py            #   MilvusVectorStore（Dense + BM25）
│
├── retrieval/
│   ├── bailian.py           #   Query Rewrite / Reranker 适配器
│   └── fusion.py            #   纯函数 RRF
│
├── evaluation/
│   └── retrieval.py         #   Precision/Recall/MRR/nDCG
│
├── generation/
│   └── bailian.py           #   BailianLLMClient（百炼 qwen-plus）
│
├── ocr/
│   └── bailian.py           #   BailianOCRClient（百炼 qwen3.5-ocr）
│
├── vision/
│   └── bailian.py           #   BailianVisionClient（百炼 qwen3-vl-flash）
│
├── config.py                #   Pydantic Settings 集中配置
├── runtime.py               #   Composition Root：装配 API 与 Worker 共用的依赖
└── worker.py                #   IngestionWorker：后台任务进程
```

## 4. 其它目录

```text
alembic/
├── env.py
└── versions/
    ├── 0001_v1_schema.py            # V1 基础表
    └── 0002_v2_async_ingestion.py   # V2 新增 ingestion_jobs 表 + 旧状态恢复

docs/
├── 1.product_description.md          # 产品描述
├── 2.technical_architecture.md       # 技术架构
├── 3.v1_implementation.md            # V1 实现说明
├── 4.v2_implementation.md            # V2 实现说明
├── 5.v3_implementation.md            # V3 实现说明
└── adr/ADR-002-...md                 # Hybrid Retrieval 架构决策

scripts/
├── smoke_v1.py / smoke_v2.py         # 历史闭环与全格式验收
├── smoke_v3.py                       # V3 高级检索全栈验收
├── rebuild_sparse_index.py           # 历史 BM25 回填
└── evaluate_retrieval.py             # 离线检索指标

tests/
├── unit/                             # 单元测试
└── fixtures/rag.md                   # 固定 Smoke Test 文档
```

## 5. 代码在哪个目录，该怎么找

| 你想找什么 | 去哪里 |
|---|---|
| 「上传文档」的业务逻辑 | `application/services.py` → `IngestionService.submit` |
| 「后台处理文档」的逻辑 | `application/services.py` → `DocumentProcessingService.process` |
| 「解析 Markdown」 | `parsers/markdown.py` |
| 「解析 PDF（含扫描页）」 | `parsers/pdf.py` |
| 「切块策略」 | `chunkers/markdown.py` |
| 「查看切块后的本地 JSON」 | `data/chunk_snapshots/{kb_id}/{document_id}/chunks.json` |
| 「向量化」 | `embeddings/bailian.py` |
| 「写/查向量库」 | `vectorstores/milvus.py` |
| 「组装答案」 | `application/services.py` → `RAGService` |
| 「拼 LLM 上下文」 | `application/context.py` → `ContextBuilder` |
| 「数据库表结构」 | `infrastructure/database/models.py` |
| 「数据库操作」 | `infrastructure/database/repository.py` |
| 「Worker 领取任务」 | `worker.py` + `repository.claim_ingestion_job` |
| 「HTTP 接口」 | `apps/api/routes.py` |
| 「配置项」 | `src/ultimate_rag/config.py` |
| 「依赖装配」 | `src/ultimate_rag/runtime.py` + `apps/api/app.py` |

## 下一步

- 想深入每个数据模型 → [核心领域模型](/architecture/data-model)
- 想逐个模块理解 → [模块总览](/modules/index)
