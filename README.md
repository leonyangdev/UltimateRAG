# UltimateRAG

一个从最小可用 RAG 持续演进为企业级知识平台的学习型工程。当前仓库实现
**V2.0 · Document Intelligence**：在保留 V1 可运行 RAG 闭环的基础上，把多种原始格式统一为
可追溯的文档领域模型，使新增 Parser 不需要修改 RAG 主流程。

## V2 能做什么

用户可以在 Web 中完成以下闭环：

1. 创建知识库
2. 上传 Markdown、PDF、DOCX、XLSX、PPTX、HTML 或常见图片
3. 自动识别 PDF 原生文本页与扫描页，并使用阿里云百炼 Qwen-OCR 处理扫描内容
4. 查看文档从 `PENDING` 到 `READY` 的处理结果和实际 Parser
5. 使用 Milvus Dense Retrieval 独立调试召回内容和分数
6. 使用阿里云百炼模型进行知识库问答
7. 查看答案引用的章节、PDF 页码、Excel 区域或 PPT 幻灯片
8. 删除文档或知识库，并同步清理三类存储

V2 明确不包含混合检索、Reranker、Agent、ACL、异步任务和 RAGOps；这些属于后续版本。

## 架构

```text
Next.js Web
    │
    ▼
FastAPI Interface
    │
    ▼
Application Services
    ├── Ingestion: Parse → Chunk → Embed → Index
    └── RAG: Query Embed → Retrieve → Context → Generate → Citation
    │
    ▼
Domain Ports
    ├── DocumentParser   → Markdown / PDF / Office / HTML / Image OCR
    ├── OCRClient        → BailianOCRClient
    ├── Chunker          → StructureAwareChunker
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
- 知识库内容按不可信输入处理，不能覆盖系统 Prompt

详细设计见 [V2 实现说明](docs/4.v2_implementation.md)，V1 的基础闭环见
[V1 实现说明](docs/3.v1_implementation.md)。

## 技术栈

- Python 3.12、FastAPI、Pydantic v2
- SQLAlchemy 2、Alembic、PostgreSQL 16
- MinIO、Milvus 2.5、Attu
- 阿里云百炼 OpenAI 兼容 API
  - Embedding 默认 `text-embedding-v4`，1024 维
  - LLM 默认 `qwen-plus`
  - OCR 默认 `qwen-vl-ocr-latest`
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
OCR_MODEL=qwen-vl-ocr-latest
OCR_MAX_IMAGE_BYTES=6291456

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
```

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

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
                                                       └→ FAILED（任一处理阶段失败）
```

失败文档保留原文件与错误状态，方便定位问题和未来重建。V2 仍是同步管线，因此上传请求会等待处理完成。

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

V2 脚本会动态生成并上传全部支持格式，验证 Parser、`READY`、带来源位置的检索、流式答案和
Citation，最后删除临时知识库及其跨存储资源。

## 目录

```text
apps/web/                         Next.js Web
apps/api/                         FastAPI 应用
src/ultimate_rag/domain/          领域模型与端口
src/ultimate_rag/application/     显式业务工作流
src/ultimate_rag/parsers/         Markdown / PDF / Office / HTML / Image 解析与注册表
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
- 图片提交 OCR 前会验证真实编码；PDF 最多 500 页，扫描页按页调用 OCR
- 用户文件名不参与本地路径或对象键构造
- `.env`、API Key 和生产凭据禁止提交
- 默认 Docker 密码只适合本地开发
- 检索内容和 LLM 输出都视为不可信数据
- 对公网部署前仍需要认证、ACL、限流与审计；这些不属于 V2 范围
