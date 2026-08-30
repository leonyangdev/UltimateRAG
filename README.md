# UltimateRAG

一个从最小可用 RAG 持续演进为企业级知识平台的学习型工程。当前仓库实现
**V2.0 · Document Intelligence**：在保留 V1 可运行 RAG 闭环的基础上，把多种原始格式统一为
可追溯的文档领域模型，使新增 Parser 不需要修改 RAG 主流程。

## V2 能做什么

用户可以在 Web 中完成以下闭环：

1. 创建知识库
2. 上传 Markdown、PDF、DOCX、XLSX、PPTX、HTML 或常见图片
3. 上传在文件与任务可靠落库后立即返回，由独立 Worker 后台处理
4. 使用本地 Docling 恢复 PDF 分栏顺序、标题、表格、图片区域和 BBox，扫描页融合百炼 OCR/Vision
5. 对独立图片融合精确文字与箭头、流程、嵌套关系，并清理 OCR 伪表格噪声
6. 前端自动刷新文档从 `PENDING` 到 `READY/FAILED` 的状态和实际 Parser
7. 使用 Milvus Dense Retrieval 独立调试召回内容和分数
8. 使用阿里云百炼模型进行知识库问答
9. 查看答案引用的章节、PDF 页码/BBox、Excel 区域或 PPT 幻灯片
10. 删除文档或知识库，并同步清理三类存储

V2 明确不包含混合检索、Reranker、Agent、ACL、DLQ 控制台和 RAGOps；这些属于后续版本。

## 架构

```text
Next.js Web
    │
    ▼
FastAPI Interface
    ├── Upload → MinIO + PostgreSQL Job → 202
    └── RAG Application → Retrieve → Context → Generate → Citation

PostgreSQL Job
    ↓
Background Worker → Parse → Chunk → Embed → Index
    │
    ▼
Domain Ports
    ├── DocumentParser   → Markdown / PDF / Office / HTML / Image Intelligence
    ├── OCRClient        → BailianOCRClient（扫描页/图片文字）
    ├── VisionClient     → BailianVisionClient（图表/架构图语义）
    ├── Chunker          → StructureAwareChunker（结构 + Token + 类型）
    ├── Embedder         → BailianEmbedder
    ├── VectorStore      → MilvusVectorStore
    ├── ObjectStorage    → MinioObjectStorage
    └── LLMClient        → BailianLLMClient

事实数据：PostgreSQL + MinIO
派生索引：Milvus
```

关键原则：

- Domain 不依赖 FastAPI、SQLAlchemy、Milvus、OpenAI SDK 或 LangChain
- PostgreSQL 保存知识库、文档状态和 Chunk 元数据
- MinIO 保存所有原始文件，且对象键由系统生成
- Milvus 只保存可重建向量索引，不作为业务事实数据源
- 文档仅在 Parse、Chunk、Embedding、Index 全部成功后进入 `READY`
- Worker 使用 PostgreSQL 租约、心跳和有限重试，进程重启不会丢失上传任务
- 知识库内容按不可信输入处理，不能覆盖系统 Prompt

详细设计见 [V2 实现说明](docs/4.v2_implementation.md)，V1 的基础闭环见
[V1 实现说明](docs/3.v1_implementation.md)。

## 技术栈

- Python 3.12、FastAPI、Pydantic v2
- Docling Layout/TableFormer、PDFium、tiktoken
- SQLAlchemy 2、Alembic、PostgreSQL 16
- MinIO、Milvus 2.5、Attu
- 阿里云百炼 OpenAI 兼容 API
  - Embedding 默认 `text-embedding-v4`，1024 维
  - LLM 默认 `qwen-plus`
  - OCR 默认 `qwen3.5-ocr`
  - PDF/独立图片理解默认 `qwen3-vl-flash`
- Next.js 16、React 19、TypeScript、Tailwind CSS 4、shadcn/ui、AI SDK
- uv、pytest、Ruff、Mypy

## 快速开始：Docker Compose

### 1. 准备模型配置

仓库根目录需要 `.env`：

```dotenv
DASHSCOPE_BASE_URL=https://你的百炼工作空间地址/compatible-mode/v1
DASHSCOPE_API_KEY=你的API-Key

# 可选覆盖
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSION=1024
LLM_MODEL=qwen-plus
OCR_MODEL=qwen3.5-ocr
OCR_MAX_IMAGE_BYTES=6291456
OCR_MAX_OUTPUT_TOKENS=4096
VISION_MODEL=qwen3-vl-flash
VISION_MAX_IMAGE_BYTES=6291456
VISION_MAX_OUTPUT_TOKENS=1536

# 可选；留空时浏览器自动访问当前页面主机的 8000 端口
NEXT_PUBLIC_API_URL=
```

不要提交 `.env`。仓库中的 `.env.example` 列出了全部配置项，但不含真实密钥。
API 容器通过 Compose 的 `env_file` 直接读取项目 `.env` 中的百炼地址和密钥，避免开发机残留的同名环境变量意外覆盖项目配置。

### 2. 启动完整系统

```bash
docker compose up -d --build
```

首次启动需要下载 PostgreSQL、MinIO、Milvus、Attu、Python 和 Node 镜像。查看状态：

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f worker
```

第一次处理文字型 PDF 时，Worker 会把 Docling Layout/TableFormer 模型下载到持久化
`docling_cache` Volume。希望在离线验收前预热模型时可执行：

```bash
docker compose run --rm worker docling-tools models download layout tableformer
```

扫描 PDF 不依赖 Docling OCR，而是按页调用 `.env` 中的百炼 OCR；低文字页还必须存在覆盖大部分
页面的栅格图才判为扫描页，避免图文页错误退化。稀疏 OCR 页会补充 Vision 关系理解。文字型 PDF
的版面与表格推理在 Worker 本地完成。默认锁文件从 PyTorch 官方 CPU Index 安装 `torch/torchvision`，避免本地 Docker
镜像误装数 GB CUDA 依赖。GPU 部署应维护独立的 CUDA 镜像/锁定策略，而不是直接修改运行时设备名。
生产环境应为 Worker 单独配置 CPU/内存与副本数。

仓库 `data/` 中的真实图片与论文 PDF 可执行专项验收（会真实产生少量百炼用量）：

```bash
uv run python scripts/smoke_v2_data.py --api-url http://localhost:8000
```

脚本断言上传立即返回、后台最终 `READY`、图片关系可召回、PDF Table 2 续块带完整多级表头，
并校验页码/BBox；默认只删除脚本自己创建的临时知识库。

### 3. 打开服务

| 服务 | 地址 | 用途 |
|---|---|---|
| Web | http://localhost:3000 | 知识库、文档、问答和检索调试 |
| FastAPI Docs | http://localhost:8000/docs | API 调试 |
| MinIO Console | http://localhost:9001 | 查看原始文件 |
| Attu | http://localhost:8001 | 查看 Milvus Collection 与向量 |

MinIO 本地开发账号由 `docker-compose.yml` 提供，只用于本地环境。生产部署必须更换。

局域网可以通过 `http://192.168.3.19:3000` 打开 Web。未设置 `NEXT_PUBLIC_API_URL` 时，
前端会自动请求 `http://192.168.3.19:8000`；API 的 CORS 配置已允许该来源。

### 4. 停止服务

```bash
docker compose down
```

保留数据卷时不要加 `-v`。只有明确希望删除全部本地数据时，才执行：

```bash
docker compose down -v
```

## 本地开发

### 后端

先只启动基础设施：

```bash
docker compose up -d postgres minio etcd milvus attu
uv sync
uv run alembic upgrade head
uv run uvicorn --app-dir apps api.app:app --reload
```

默认本地配置在 `.env.example` 中。Alembic 是唯一数据库 Schema 变更入口，应用启动不会自动建表。

### 前端

```bash
cd apps/web
npm install
npm run dev
```

未设置 `NEXT_PUBLIC_API_URL` 时，浏览器使用当前页面主机的 `8000` 端口。例如从
`192.168.3.19:3000` 打开页面时会请求 `192.168.3.19:8000`。如果 API 使用独立域名，需在
`.env` 设置 `NEXT_PUBLIC_API_URL` 并重新构建前端。

## API 概览

### Knowledge Base

```text
POST   /api/knowledge-bases
GET    /api/knowledge-bases
GET    /api/knowledge-bases/{id}
DELETE /api/knowledge-bases/{id}
```

### Document

```text
POST   /api/knowledge-bases/{id}/documents
GET    /api/knowledge-bases/{id}/documents
GET    /api/documents/{id}
DELETE /api/documents/{id}
```

### Retrieval 与 Chat

```text
POST /api/retrieval/search
POST /api/chat
POST /api/chat/stream
```

检索请求：

```json
{
  "knowledge_base_id": "kb-id",
  "query": "BGE-M3 是什么？",
  "top_k": 5
}
```

问答请求使用 `question` 字段。响应同时包含 `answer`、`citations` 和用于学习调试的
`retrieval_results`。

## 文档处理状态

上传接口返回 `202 Accepted` 和 `PENDING` 文档，不等待解析。Worker 使用以下状态推进：

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
   ↑                                                       
   └── 临时故障有限重试                         任一终态错误 → FAILED
```

前端仅在存在非终态文档时每两秒自动刷新。失败文档保留原文件、Chunk 事实与可操作错误；Milvus
半成品会清理，检索还会按 PostgreSQL `READY` 状态二次过滤。

## 验证

```bash
# 后端
uv run pytest
uv run ruff check .
uv run mypy

# 前端
cd apps/web
npm run lint
npm run build
npm audit
```

单元测试会在内存中生成各类格式，避免提交二进制 Fixture。固定 Markdown Smoke Test 文档位于
`tests/fixtures/rag.md`，推荐问题是“BGE-M3 是什么？”。

启动 Docker 全栈后，执行真实 PostgreSQL、MinIO、Milvus 和百炼闭环验收：

```bash
uv run python scripts/smoke_v1.py --api-url http://localhost:8000
```

V1 脚本保留用于回归。V2 全格式验收使用：

```bash
uv run python scripts/smoke_v2.py --api-url http://localhost:8000
```

V2 脚本会动态生成全部支持格式，先验证上传立即返回 `202/PENDING`，再轮询 Worker 到 `READY`，
最后验证来源位置、检索、流式答案和 Citation，并删除临时知识库及其跨存储资源。

## 目录

```text
apps/web/                         Next.js Web
apps/api/                         FastAPI 应用
src/ultimate_rag/domain/          领域模型与端口
src/ultimate_rag/application/     显式业务工作流
src/ultimate_rag/parsers/         Markdown / PDF / Office / HTML / Image 解析与注册表
src/ultimate_rag/worker.py        PostgreSQL 持久化任务 Worker
src/ultimate_rag/runtime.py       API/Worker 共用依赖装配
src/ultimate_rag/vision/          百炼图片语义理解适配器
src/ultimate_rag/ocr/             百炼 OCR 适配器
src/ultimate_rag/chunkers/        结构感知切块
src/ultimate_rag/embeddings/      百炼向量适配器
src/ultimate_rag/vectorstores/    Milvus 适配器
src/ultimate_rag/generation/      百炼 LLM 适配器
src/ultimate_rag/infrastructure/  PostgreSQL / MinIO
alembic/                          数据库迁移
scripts/                          可重复执行的发布验收脚本
tests/                            单元测试与固定文档
docs/                             产品、架构与实现文档
```

## 安全提醒

- 上传文件最大 10 MB；Markdown/HTML 必须使用 UTF-8，Office 会检查 ZIP Bomb 风险
- 图片提交模型前会验证/压缩；PDF 最多 500 页，扫描页按页 OCR，附图数量和并发均有界
- 用户文件名不参与本地路径或对象键构造
- `.env`、API Key 和生产凭据禁止提交
- 默认 Docker 密码只适合本地开发
- 检索内容和 LLM 输出都视为不可信数据
- 对公网部署前仍需要认证、ACL、限流与审计；这些不属于 V2 范围
