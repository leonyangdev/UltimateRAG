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

删除知识库及其文档、Chunk、任务、向量（跨三类存储同步清理）。

## 3. 文档

### `POST /api/knowledge-bases/{knowledge_base_id}/documents` → 202

上传文档（multipart/form-data，字段名 `file`）。**返回 202，不等待处理完成。**

```json
// 响应：status 初始为 pending
{
  "id": "doc-123",
  "knowledge_base_id": "kb-1",
  "filename": "rag-intro.md",
  "mime_type": "text/markdown",
  "extension": ".md",
  "sha256": "9f86d08...",
  "status": "pending",
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

### `DELETE /api/documents/{document_id}` → 204

删除文档原文件、Chunk 和派生向量。文档正在处理时 → 409。

## 4. 检索与问答

### `POST /api/retrieval/search` → 200

纯 Dense Retrieval，**不调用 LLM**，可独立调试检索效果。

```json
// 请求
{
  "knowledge_base_id": "kb-1",
  "query": "什么是 RAG",
  "top_k": 5
}
```

```json
// 响应：按相似度降序
[
  {
    "chunk_id": "chunk-abc",
    "document_id": "doc-123",
    "filename": "rag-intro.md",
    "content": "检索增强生成（RAG）...",
    "heading_path": ["RAG", "什么是 RAG"],
    "locator": { "heading_path": ["RAG"], "page": 2, "bbox": null, "sheet": null, "cell_range": null, "slide": null },
    "score": 0.87
  }
]
```

字段校验：`query` 1–4000 字符，`top_k` 1–20。

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
  "answer": "根据当前知识库，RAG 是检索增强生成... [来源 1]",
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
  ]
}
```

### `POST /api/chat/stream` → 200 (text/event-stream)

流式 RAG 问答，使用 **AI SDK Data Stream Protocol** 的 SSE 表示。

事件顺序：

```text
data: {"type":"start","messageId":"msg-..."}
data: {"type":"start-step"}
data: {"type":"data-retrieval","data":{"citations":[...],"retrieval_results":[...]}}
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

## 5. HTTP 状态码与业务异常映射

| 状态码 | 业务异常 | 场景 |
|---|---|---|
| `400` | `InvalidDocumentError` | 文件超限、格式不支持、空文档、参数不合法 |
| `404` | `ResourceNotFoundError` | 知识库/文档不存在 |
| `409` | `DocumentBusyError` | 文档正在后台处理，完成或失败后才能删除 |
| `502` | `UltimateRAGError`（其他已知） | 外部处理故障；不暴露 Stack Trace |
| `500` | 未预期异常 | 兜底（FastAPI 默认） |

## 6. 通用字段说明

| 字段 | 说明 |
|---|---|
| `status` | 文档处理状态：`pending`/`parsing`/`chunking`/`embedding`/`indexing`/`ready`/`failed` |
| `error_message` | 失败或重试中的可读说明 |
| `parser_name` / `parser_version` | 实际使用的解析器 |
| `locator` | 跨格式原文位置；不同文档类型只填适用字段 |
| `score` | COSINE 相似度（与建库时同一 Embedding 模型才有语义可比性） |

## 下一步

- 端点在代码里的完整实现 → [API 与前端](/modules/api-web)
- 一个请求的完整旅程 → [检索问答全流程](/workflows/query)
