# 配置系统

UltimateRAG 使用 **Pydantic Settings** 集中管理配置，所有配置从**环境变量或 `.env` 文件**读取。

代码位置：`src/ultimate_rag/config.py`

## 1. 设计要点

- **类型安全**：所有配置都有 Python 类型和默认值，错误配置在启动时就能被发现
- **进程级只读**：`get_settings()` 使用 `@lru_cache`，整个进程只解析一次环境变量
- **统一装配**：API 和 Worker 从同一个 `Settings` 装配依赖，保证两套进程配置一致
- **敏感值不落库**：API Key 只从 `.env` 注入，`.env.example` 只含占位值

```python
from ultimate_rag.config import get_settings

settings = get_settings()   # 进程内单例，只解析一次
```

## 2. 配置分组

配置按用途分为几组，下面按组列出（默认值面向本地 Docker Compose 环境）：

### 基础

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | `UltimateRAG` | 应用名 |
| `ENVIRONMENT` | `development` | 运行环境 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `CORS_ORIGINS` | `["http://localhost:3000", ...]` | 跨域白名单 |

### 数据库与存储

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...localhost:5432/ultimate_rag` | PostgreSQL 连接串 |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO 地址 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 本地开发账号 | MinIO 凭据（生产必须更换） |
| `MINIO_BUCKET` | `documents` | 原始文件桶 |
| `MILVUS_URI` | `http://localhost:19530` | Milvus 地址 |
| `MILVUS_COLLECTION` | `knowledge_chunks` | Dense Collection |
| `MILVUS_SPARSE_COLLECTION` | `knowledge_chunks_sparse_v3` | BM25 Sparse Collection |
| `BM25_K1` / `BM25_B` | `1.2` / `0.75` | BM25 基线参数；修改后需显式重建 Sparse 索引 |
| `CHUNK_SNAPSHOT_DIR` | `data/chunk_snapshots` | Embedding 前最终 Chunk 明文 JSON 目录 |

### 模型（阿里云百炼）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 百炼 OpenAI 兼容端点 |
| `DASHSCOPE_API_KEY` | **必填** | API Key，只从 `.env` 提供 |
| `EMBEDDING_MODEL` | `text-embedding-v4` | Embedding 模型 |
| `EMBEDDING_DIMENSION` | `1024` | 向量维度（必须与 Milvus 一致） |
| `EMBEDDING_BATCH_SIZE` | `10` | 每批向量化数量 |
| `LLM_MODEL` | `qwen-plus` | 问答模型 |
| `QUERY_REWRITE_MODEL` | `qwen-plus` | 结构化查询改写模型 |
| `RERANK_MODEL` | `qwen3-rerank` | 文本重排模型 |
| `RERANK_URL` | 百炼 `/compatible-api/v1/reranks` | Qwen3 Rerank API 地址 |
| `RERANK_MAX_REQUEST_TOKENS` | `120000` | 总请求 Token 硬上限，实际还预留 10% 估算余量 |
| `OCR_MODEL` | `qwen3.5-ocr` | OCR 模型 |
| `VISION_MODEL` | `qwen3-vl-flash` | 图片语义理解模型 |
| `OCR_MAX_IMAGE_BYTES` | `6MB` | OCR 单图上限（百炼 Base64 限制约 7MB） |
| `VISION_MAX_IMAGE_BYTES` | `6MB` | 视觉单图上限 |
| `OCR_MAX_OUTPUT_TOKENS` | `4096` | OCR 有界输出，防止伪表格无限膨胀 |
| `VISION_MAX_OUTPUT_TOKENS` | `1536` | 视觉语义描述输出上限 |
| `MODEL_TIMEOUT_SECONDS` | `60` | 模型请求超时 |

### 切块与检索

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `MAX_UPLOAD_BYTES` | `10MB` | 上传文件上限 |
| `CHUNK_MAX_TOKENS` | `512` | Chunk Token 预算（64–8192） |
| `CHUNK_OVERLAP_TOKENS` | `64` | Chunk 重叠 Token |
| `CHUNK_TOKENIZER` | `cl100k_base` | 本地 Token 预算近似器 |
| `RETRIEVAL_TOP_K` | `5` | 默认召回数 |
| `RETRIEVAL_CANDIDATE_K` | `30` | 融合/重排前候选宽度 |
| `RETRIEVAL_RRF_K` | `60` | RRF 排名平滑常数 |
| `RETRIEVAL_QUERY_REWRITE` | `true` | 默认是否启用查询改写 |
| `RETRIEVAL_RERANK` | `true` | 默认是否启用二阶段重排 |
| `RETRIEVAL_PARENT_EXPANSION` | `true` | 默认是否启用 Small2Big |
| `RETRIEVAL_PARENT_WINDOW` | `1` | Parent 内前后 Child 窗口（0–3） |
| `RETRIEVAL_PARENT_MAX_TOKENS` | `1536` | 单条扩展上下文 Token 上限 |
| `CONTEXT_MAX_CHARS` | `12000` | LLM 上下文最大字符数 |
| `SUMMARY_MAX_CHUNKS` | `24` | 全文总结最多覆盖的章节 Chunk 数 |
| `SUMMARY_MAX_TOKENS` | `16000` | 全文总结证据 Token 预算 |
| `SUMMARY_CONTEXT_MAX_CHARS` | `64000` | 全文总结独立 Context 字符预算 |
| `CHAT_RECENT_TOKEN_BUDGET` | `6000` | 每轮保留的最近会话原文 Token 预算 |
| `CHAT_MEMORY_MAX_TOKENS` | `1600` | 递归长期记忆最大输出 Token |
| `CHAT_GENERATION_STALE_SECONDS` | `600` | PENDING 生成超时恢复阈值 |

### Worker 任务

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `INGESTION_JOB_MAX_ATTEMPTS` | `3` | 最大尝试次数（1–10） |
| `WORKER_POLL_INTERVAL_SECONDS` | `1.0` | 空队列轮询间隔 |
| `WORKER_LEASE_SECONDS` | `900` | 任务租约时长 |
| `WORKER_HEARTBEAT_SECONDS` | `30` | 心跳续租间隔 |
| `WORKER_RETRY_DELAY_SECONDS` | `10` | 重试基础延迟 |

### PDF 解析

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PDF_NATIVE_TEXT_THRESHOLD` | `20` | 扫描候选页原生文字阈值 |
| `PDF_SCAN_IMAGE_COVERAGE_THRESHOLD` | `0.65` | 扫描候选页最大栅格图覆盖率阈值 |
| `PDF_SCAN_VISION_TEXT_THRESHOLD` | `300` | OCR 低于该字符数时补视觉理解，0 为关闭 |
| `PDF_RENDER_SCALE` | `2.0` | 扫描页渲染缩放 |
| `PDF_VISION_CONCURRENCY` | `2` | 图片理解并发 |
| `PDF_MAX_PICTURES` | `20` | 单 PDF 最大附图数 |
| `PDF_MIN_PICTURE_PIXELS` | `10000` | 附图最小像素 |
| `DOCLING_DEVICE` | `cpu` | Docling 推理设备 |
| `DOCLING_NUM_THREADS` | `4` | Docling 线程数 |
| `DOCLING_TIMEOUT_SECONDS` | `600` | Docling 超时 |

## 3. 校验规则

配置里有三个跨字段校验（`validate_cross_field_limits`）：

- `chunk_overlap_tokens` 必须小于 `chunk_max_tokens`
- `retrieval_parent_max_tokens` 必须不小于 `chunk_max_tokens`
- `worker_heartbeat_seconds` 必须小于 `worker_lease_seconds`

违反会在启动时报错，而不是在运行时才暴露。

## 4. 完整配置示例

参考仓库根目录 `.env.example`：

```dotenv
# 必填：百炼地址与密钥
DASHSCOPE_BASE_URL=https://你的百炼工作空间地址/compatible-mode/v1
DASHSCOPE_API_KEY=你的API-Key

# 可选覆盖（省略时用默认值）
EMBEDDING_MODEL=text-embedding-v4
LLM_MODEL=qwen-plus
OCR_MODEL=qwen3.5-ocr
VISION_MODEL=qwen3-vl-flash

# Chunk 明文诊断快照（Docker Compose 会覆盖为容器 bind mount 目标）
CHUNK_SNAPSHOT_DIR=data/chunk_snapshots

# 前端
NEXT_PUBLIC_API_URL=
```

::: warning 安全提醒
`.env`、真实 API Key、生产凭据**禁止提交到 Git**。`.env.example` 只能包含安全占位值。
`CHUNK_SNAPSHOT_DIR` 中保存的是文档明文，必须限制目录权限、监控磁盘容量，并保持 Git 忽略。
:::

## 下一步

- 想知道这些配置怎么被装配成真实对象 → [模块总览](/modules/index) 或 [整体架构与分层](/architecture/overview)
