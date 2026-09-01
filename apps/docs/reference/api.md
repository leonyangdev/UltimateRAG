# REST API 参考

Base URL：`http://localhost:8000`（前端默认解析为「当前主机名 + 8000 端口」）

所有响应为 JSON；错误统一使用：

```json
{ "detail": "可读错误信息" }
```

## 1. 健康检查

### `GET /api/health`

返回进程存活状态，不触发昂贵的模型调用。

```json
{ "status": "ok" }
```

## 2. 知识库

### `POST /api/knowledge-bases` → 201

创建知识库。

```json
// 请求
{ "name": "产品手册", "description": "产品文档知识库" }

// 响应
{
  "id": "a1b2...",
  "name": "产品手册",
  "description": "产品文档知识库",
  "created_at": "2026-08-29T10:00:00Z",
  "updated_at": "2026-08-29T10:00:00Z"
}
```

字段校验：`name` 1–200 字符，`description` ≤2000 字符。

### `GET /api/knowledge-bases` → 200

列出全部知识库（创建时间倒序）。

### `GET /api/knowledge-bases/{knowledge_base_id}` → 200

读取单个知识库；不存在 → 404。

### `DELETE /api/knowledge-bases/{knowledge_base_id}` → 204

删除知识库及其文档、Chunk、任务、向量和本地 Chunk 明文快照（跨存储同步清理）。

## 3. 文档

### `POST /api/knowledge-bases/{knowledge_base_id}/documents` → 202

上传文档（multipart/form-data，字段名 `file`）。**返回 202，不等待处理完成。**

```json
// 响应：status 初始为 PENDING
{
  "id": "doc-123",
  "knowledge_base_id": "kb-1",
  "filename": "rag-intro.md",
  "mime_type": "text/markdown",
  "extension": ".md",
  "sha256": "9f86d08...",
  "status": "PENDING",
  "parser_name": null,
  "parser_version": null,
  "error_message": null,
  "created_at": "...",
  "updated_at": "..."
}
```

支持格式：Markdown / PDF / Word / Excel / PowerPoint / HTML / 图片。

### `GET /api/knowledge-bases/{knowledge_base_id}/documents` → 200

列出知识库文档 + 实时处理状态（前端轮询用）。

### `GET /api/documents/{document_id}` → 200

读取单份文档的元数据与状态。

### `POST /api/documents/{document_id}/reindex` → 202

复用 MinIO 原文件重新提交 Parser、Asset、Chunk 和向量索引。仅 READY/FAILED 可提交；处理中重复
提交返回 409。适用于 Parser 升级后给存量 PDF 回填图片资源，无需用户重新上传。

### `DELETE /api/documents/{document_id}` → 204

删除文档原文件、Chunk 和派生向量。文档正在处理时 → 409。

### `GET /api/chunks/{chunk_id}/preview` → 200 (image/jpeg)

按 PostgreSQL 中保存的页码/BBox，从 MinIO 原 PDF 本地渲染命中区域。接口不接受自定义裁剪参数；
非 PDF、非 READY、无页码或不存在的 Chunk 返回 404。响应支持 `ETag`、`If-None-Match` 与私有缓存。

### `GET /api/assets/{asset_id}/content` → 200

返回摄取期已从 READY 文档抽取的图片。Asset ID 必须存在于 PostgreSQL；服务端据此读取系统
Object Key，接口不接受任意 MinIO Key。响应 MIME 来自持久化事实，支持强 ETag、304、私有缓存
和 `nosniff`。

## 4. 检索与问答

### `POST /api/retrieval/search` → 200

执行 V3 Retrieval，**不调用答案生成 LLM**。为兼容 V1/V2 客户端仍返回结果数组；未提供高级
字段时使用部署默认值（Hybrid + Rewrite + Rerank + Small2Big）。

```json
// 请求
{
  "knowledge_base_id": "kb-1",
  "query": "什么是 RAG",
  "top_k": 5,
  "mode": "hybrid",
  "candidate_k": 30,
  "enable_query_rewrite": true,
  "enable_rerank": true,
  "enable_parent_expansion": true,
  "document_ids": ["optional-doc-id"]
}
```

```json
// 响应：按最终阶段分数降序
[
  {
    "chunk_id": "chunk-abc",
    "document_id": "doc-123",
    "filename": "rag-intro.md",
    "content": "检索增强生成（RAG）...",
    "heading_path": ["RAG", "什么是 RAG"],
    "locator": { "heading_path": ["RAG"], "page": 2, "bbox": null, "sheet": null, "cell_range": null, "slide": null },
    "score": 0.91,
    "dense_score": 0.87,
    "sparse_score": 8.42,
    "fusion_score": 0.0325,
    "rerank_score": 0.91,
    "retrieval_sources": ["dense:original", "sparse:original"],
    "matched_content": "检索增强生成（RAG）...",
    "context_chunk_ids": ["chunk-before", "chunk-abc"],
    "content_types": ["TABLE"],
    "preview_url": "/api/chunks/chunk-abc/preview",
    "assets": [
      {
        "id": "asset-123",
        "kind": "IMAGE",
        "media_type": "image/jpeg",
        "filename": "figure-page-3-1.jpg",
        "title": "Transformer 架构图",
        "description": "图中左侧为 Encoder，右侧为 Decoder……",
        "locator": { "heading_path": [], "page": 3, "bbox": [18.0, 80.0, 594.0, 554.0], "sheet": null, "cell_range": null, "slide": null },
        "content_url": "/api/assets/asset-123/content"
      }
    ]
  }
]
```

字段校验：非空 `query` 最多 4000 字符，`top_k` 1–20，`candidate_k` 1–100，`document_ids`
最多 50 个。`mode` 只能是 `dense`、`sparse` 或 `hybrid`。

### `POST /api/retrieval/explain` → 200

请求与 `/retrieval/search` 相同，响应增加阶段 Trace，推荐 Retrieval Playground 和调参工具使用：

```json
{
  "results": [{ "chunk_id": "chunk-abc", "score": 0.91 }],
  "trace": {
    "original_query": "什么是 RAG",
    "query_variants": ["什么是 RAG", "检索增强生成 RAG 定义"],
    "mode": "hybrid",
    "candidate_count": 18,
    "result_count": 5,
    "rewrite_applied": true,
    "rerank_applied": true,
    "parent_expansion_applied": true,
    "fallback_reasons": []
  }
}
```

`fallback_reasons` 非空表示发生了明确降级；它不是 V5 的持久化全链路 Trace。

### `POST /api/chat` → 200

完整 RAG 问答（非流式），返回答案 + 引用 + 召回证据。

```json
// 请求（注意字段名是 question）
{
  "knowledge_base_id": "kb-1",
  "question": "什么是 RAG？",
  "top_k": 5
}

// 响应
{
  "answer": "图示如下：\n\n![Transformer 架构图](asset://asset-123)\n\n[来源 1](citation://1)",
  "citations": [
    {
      "document_id": "doc-123",
      "filename": "rag-intro.md",
      "chunk_id": "chunk-abc",
      "heading_path": ["RAG"],
      "locator": { "heading_path": ["RAG"], "page": 2, "bbox": null, "sheet": null, "cell_range": null, "slide": null }
    }
  ],
  "retrieval_results": [
    { "chunk_id": "chunk-abc", "document_id": "doc-123", "filename": "rag-intro.md", "content": "...", "heading_path": ["RAG"], "locator": null, "score": 0.87 }
  ],
  "retrieval_trace": { "mode": "hybrid", "candidate_count": 18, "result_count": 5, "fallback_reasons": [] }
}
```

### `POST /api/chat/stream` → 200 (text/event-stream)

流式 RAG 问答，使用 **AI SDK Data Stream Protocol** 的 SSE 表示。

事件顺序：

```text
data: {"type":"start","messageId":"msg-..."}
data: {"type":"start-step"}
data: {"type":"data-retrieval","data":{"citations":[...],"retrieval_results":[...],"retrieval_trace":{...}}}
data: {"type":"text-start","id":"text-..."}
data: {"type":"text-delta","id":"text-...","delta":"根据"}
data: {"type":"text-delta","id":"text-...","delta":"当前知识库"}
...（更多 text-delta）
data: {"type":"text-end","id":"text-..."}
data: {"type":"finish-step"}
data: {"type":"finish","finishReason":"stop"}
data: [DONE]
```

响应头：

```text
Content-Type: text/event-stream
X-Accel-Buffering: no                 # 关闭代理缓冲，token 及时到达
x-vercel-ai-ui-message-stream: v1     # AI SDK 识别标记
```

生成中断时，浏览器会收到：

```json
{ "type": "text-end", "id": "text-..." }
{ "type": "error", "errorText": "生成过程中断，请稍后重试。" }
```

## 5. 会话 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/chat-sessions` | 持久化新会话；网页端仅在 Draft 首次 Chat 发送前调用 |
| `GET` | `/api/knowledge-bases/{id}/chat-sessions` | 按最近活动列出历史会话 |
| `GET` | `/api/chat-sessions/{session_id}` | 返回会话、完整消息和助手消息的 Retrieval Evidence |
| `DELETE` | `/api/knowledge-bases/{id}/chat-sessions/{session_id}` | 删除会话及全部消息，成功返回 204 |

网页中的 Draft 没有服务端资源，也没有对应 API：进入知识库、点击“新建对话”以及在 Draft 中重复
点击都不会写数据库。首次提交 Chat 问题时，客户端先调用会话 POST，随后把响应中的 `id` 作为
`session_id` 传给 `/api/chat` 或 `/api/chat/stream`；删除最后一条历史会话后重新回到 Draft。

新版 `/api/chat` 与 `/api/chat/stream` 可传 `session_id`。后端会验证会话属于请求中的知识库；
不传时保留旧版无状态行为。全文总结的 `retrieval_trace` 还会返回：

```json
{
  "intent": "document_summary",
  "strategy": "structural_coverage"
}
```

正常完成的助手消息会在 `retrieval_evidence` 中保存当时的 `citations`、`retrieval_results` 和
`retrieval_trace`。这是展示历史来源的快照，不会在读取会话时重新调用 Milvus 或 LLM。

删除会话时同时校验父知识库和会话归属；知识库/会话不存在或跨知识库访问返回 404。Repository
会先锁定会话并检查助手 PENDING 占位，有效生成尚未完成时返回 409；超过部署失活窗口的占位
不会永久阻塞删除。父会话与全部 `chat_messages` 在同一 PostgreSQL 事务中级联删除，不影响知识库
文档、Chunk 或向量索引。

## 6. HTTP 状态码与业务异常映射

| 状态码 | 业务异常 | 场景 |
|---|---|---|
| `400` | `InvalidDocumentError` | 文件超限、格式不支持、空文档、参数不合法 |
| `404` | `ResourceNotFoundError` | 知识库/文档/会话不存在，或会话不属于路径中的知识库 |
| `409` | `DocumentBusyError` / `ChatSessionBusyError` | 文档正在处理，或会话仍在生成回答 |
| `502` | `UltimateRAGError`（其他已知） | 外部处理故障；不暴露 Stack Trace |
| `500` | 未预期异常 | 兜底（FastAPI 默认） |

## 7. 通用字段说明

| 字段 | 说明 |
|---|---|
| `status` | 文档处理状态：`PENDING`/`PARSING`/`CHUNKING`/`EMBEDDING`/`INDEXING`/`READY`/`FAILED` |
| `error_message` | 失败或重试中的可读说明 |
| `parser_name` / `parser_version` | 实际使用的解析器 |
| `locator` | 跨格式原文位置；不同文档类型只填适用字段 |
| `score` | 当前最终排序所用分数；可能来自 Dense、BM25、RRF 或 Rerank，不能跨请求比较 |
| `dense_score` / `sparse_score` | 各召回通道原始分数 |
| `fusion_score` / `rerank_score` | 融合与二阶段重排分数；未执行阶段为 `null` |
| `context_chunk_ids` | Small2Big 实际进入上下文的 Child ID；Citation 仍锚定命中 `chunk_id` |
| `content_types` | 命中 Child 的结构类型，如 `TEXT`、`TABLE`、`IMAGE` |
| `preview_url` | 有 PDF 页码时返回受控局部预览路径，否则为 `null` |
| `assets` | 命中 Chunk 关联的可展示资源元数据；只公开受控 `content_url`，不含 Object Key |

## 下一步

- 端点在代码里的完整实现 → [API 与前端](/modules/api-web)
- 一个请求的完整旅程 → [检索问答全流程](/workflows/query)
