# 配置项速查

配置由 **Pydantic Settings** 管理，从环境变量或 `.env` 读取。完整分组说明见 [配置系统](/architecture/config)。这里是**速查表**：想改某个行为，找对应变量。

代码位置：`src/ultimate_rag/config.py`

## 1. 必填

| 环境变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼 API Key，只从 `.env` 提供，**禁止提交 Git** |

## 2. 按「想做什么」查变量

| 想做什么 | 改哪个变量 |
|---|---|
| 限制上传文件大小 | `MAX_UPLOAD_BYTES`（默认 10MB） |
| 调整 Chunk 大小 | `CHUNK_MAX_TOKENS`（512）、`CHUNK_OVERLAP_TOKENS`（64） |
| 换问答模型 | `LLM_MODEL`（qwen-plus） |
| 换 Embedding 模型 | `EMBEDDING_MODEL`（text-embedding-v4）、`EMBEDDING_DIMENSION`（1024） |
| 换 Query Rewrite / Rerank 模型 | `QUERY_REWRITE_MODEL`（qwen-plus）、`RERANK_MODEL`（qwen3-rerank） |
| 收紧 Rerank 请求预算 | `RERANK_MAX_REQUEST_TOKENS`（120000，Adapter 另留 10% 余量） |
| 换 OCR / 视觉模型 | `OCR_MODEL`（qwen3.5-ocr）、`VISION_MODEL`（qwen3-vl-flash） |
| 调整高级检索候选与融合 | `RETRIEVAL_CANDIDATE_K`（30）、`RETRIEVAL_RRF_K`（60） |
| 默认关闭可选线上阶段 | `RETRIEVAL_QUERY_REWRITE`、`RETRIEVAL_RERANK` |
| 调整 Small2Big | `RETRIEVAL_PARENT_EXPANSION`、`RETRIEVAL_PARENT_WINDOW`、`RETRIEVAL_PARENT_MAX_TOKENS` |
| 调整 BM25 | `BM25_K1`（1.2）、`BM25_B`（0.75）；改后显式重建 Sparse Collection |
| 增大检索上下文 | `CONTEXT_MAX_CHARS`（12000） |
| 增加任务重试次数 | `INGESTION_JOB_MAX_ATTEMPTS`（3） |
| 加快/放慢 Worker 轮询 | `WORKER_POLL_INTERVAL_SECONDS`（1.0） |
| 调整租约与心跳 | `WORKER_LEASE_SECONDS`（900）、`WORKER_HEARTBEAT_SECONDS`（30） |
| 重试更快/更慢 | `WORKER_RETRY_DELAY_SECONDS`（10） |
| PDF 扫描页判定灵敏度 | `PDF_NATIVE_TEXT_THRESHOLD`（20） |
| Docling 推理设备 | `DOCLING_DEVICE`（cpu）、`DOCLING_NUM_THREADS`（4） |
| 模型请求超时 | `MODEL_TIMEOUT_SECONDS`（60） |
| 跨域白名单 | `CORS_ORIGINS` |

## 3. 外部服务连接

| 环境变量 | 默认值 |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://ultimate_rag:ultimate_rag@localhost:5432/ultimate_rag` |
| `MINIO_ENDPOINT` / `MINIO_BUCKET` | `localhost:9000` / `documents` |
| `MILVUS_URI` / `MILVUS_COLLECTION` | `http://localhost:19530` / `knowledge_chunks`（Dense） |
| `MILVUS_SPARSE_COLLECTION` | `knowledge_chunks_sparse_v3` |
| `RERANK_URL` | `https://dashscope.aliyuncs.com/compatible-api/v1/reranks` |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

## 4. 跨字段校验（违反即启动失败）

- `CHUNK_OVERLAP_TOKENS` 必须小于 `CHUNK_MAX_TOKENS`
- `RETRIEVAL_PARENT_MAX_TOKENS` 必须不小于 `CHUNK_MAX_TOKENS`
- `RERANK_MAX_REQUEST_TOKENS` 必须位于 10000–120000
- `WORKER_HEARTBEAT_SECONDS` 必须小于 `WORKER_LEASE_SECONDS`

## 5. 前端

| 环境变量 | 说明 |
|---|---|
| `NEXT_PUBLIC_API_URL` | 浏览器访问的 API 地址；留空则默认「当前主机名 + 8000 端口」 |

## 6. 最小 `.env.example`

```dotenv
DASHSCOPE_BASE_URL=https://你的百炼工作空间地址/compatible-mode/v1
DASHSCOPE_API_KEY=你的API-Key

NEXT_PUBLIC_API_URL=
```

::: warning
`.env` 与真实密钥禁止提交到 Git。生产部署必须覆盖默认凭据与 `DOCLING_DEVICE`。
:::
