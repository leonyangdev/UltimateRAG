# 文档摄取全流程

这条链路回答：**一份文件上传后，到「可被检索」之间发生了什么？**

## 1. 全景图

```text
浏览器 / curl
   │  ① POST /knowledge-bases/{kb_id}/documents (multipart)
   ▼
FastAPI Route                     ←──── Interface 层
   │  ② 有界读取 + 校验
   ▼
IngestionService.submit           ←──── Application 层
   │  ③ 存 MinIO → ④ 创建 Document + IngestionJob（同事务）
   ▼
返回 202 + {status: PENDING}       ←── 上传请求到此结束，HTTP 立即返回
        │
        │  ===== 后台独立进程（Worker）=====
        ▼
IngestionWorker.run_once
   │  ⑤ claim_ingestion_job（FOR UPDATE SKIP LOCKED）
   ▼
DocumentProcessingService.process  ←──── 核心管线
   │  ⑥ PARSING  → Parse（Parser 解析原文件）
   │  ⑦ CHUNKING → Chunk（切块）+ Asset（图片持久化）
   │  ⑧ SNAPSHOT → 本地原子保存最终 Chunk + metadata
   │  ⑨ EMBEDDING→ Embed（向量化）
   │  ⑩ INDEXING → 写 PostgreSQL Chunk + Milvus 向量
   ▼
   ⑪ READY  ←────────────────── 全部成功，文档可被检索
```

## 2. 第 1 步：上传请求

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/documents \
  -F "file=@report.md"
```

Route 做两件事：

1. **有界读取**：`file.read(max_upload_bytes + 1)`，超限在 HTTP 边界立即 400
2. 调用 `IngestionService.submit(kb_id, filename, mime_type, content)`

## 3. 第 2 步：submit —— 可靠入队

```python
async def submit(self, knowledge_base_id, filename, mime_type, content):
    # ① 校验知识库、文件名（只用 basename 防路径穿越）、大小、MIME
    await self._repository.get_knowledge_base(knowledge_base_id)
    safe_filename = PurePath(filename).name

    # ② 生成系统对象键 + SHA-256 指纹（不信任用户文件名）
    document_id = str(uuid4())
    object_key = f"{knowledge_base_id}/{document_id}/source{extension}"

    # ③ 提前确认有 Parser 能处理（避免为未知格式产生 MinIO 孤儿对象）
    self._parser_registry.resolve(DocumentSource(...))

    # ④ 先存原文件，再在同一事务创建 Document + IngestionJob
    await self._storage.put(object_key, content, mime_type)
    try:
        document = await self._repository.create_document_with_job(...)
    except Exception:
        await self._storage.delete(object_key)   # 补偿：删掉孤儿对象
        raise
    return document      # 返回 PENDING，不等待解析
```

::: tip 三个关键保证
- **原文件先落盘**：失败后可用原文件排查、重建
- **Document + Job 同事务**：杜绝「有文档没任务」的丢任务窗口
- **失败补偿删除**：不产生无法追踪的 MinIO 孤儿对象
:::

## 4. 第 3 步：Worker 领取任务

Worker 是独立进程（`uv run python -m ultimate_rag.worker`），循环执行：

```sql
-- 等价查询：跳过别人正在处理的行，领取最早可用任务
SELECT * FROM ingestion_jobs
WHERE attempts < max_attempts
  AND (
    (status = 'PENDING' AND available_at <= now())
    OR (status = 'RUNNING' AND locked_at <= now() - lease_seconds)  -- 回收过期租约
  )
ORDER BY available_at, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

领取成功后：

```python
job = await self._repository.claim_ingestion_job(self._worker_id, lease_seconds=...)
if job is None:
    return False          # 空队列，退避后重试

heartbeat = asyncio.create_task(self._heartbeat(job, lease_lost))  # 后台续租
try:
    await self._processor.process(job.document_id)   # 真正干活
...
```

- **心跳续租**：处理期间定期把租约续到「现在 + lease_seconds」
- **租约丢失**：立即停止提交状态，交给新 Worker 接管

## 5. 第 4 步：核心处理管线

这是入库链路的心脏，由 `DocumentProcessingService.process` 编排：

```python
async def process(self, document_id) -> Document:
    document = await self._repository.get_document(document_id)
    if document.status == DocumentStatus.READY:
        return document      # 幂等：已 READY 直接返回

    content = await self._storage.get(document.object_key)     # 读原文件
    source = DocumentSource(document.id, document.filename, document.mime_type, content)
    parser = self._parser_registry.resolve(source)

    # ── 阶段 1：Parse ─────────────────────────────────────────
    await self._repository.update_document_status(document.id, DocumentStatus.PARSING, ...)
    parsed = await parser.parse(source)

    # ── 阶段 2：Chunk ─────────────────────────────────────────
    await self._repository.update_document_status(document.id, DocumentStatus.CHUNKING)
    chunks = await self._chunker.split(parsed, document.knowledge_base_id)
    if not chunks:
        raise InvalidDocumentError("文档没有生成任何 Chunk")    # 空文档不调用付费 API

    # PDF 图片：稳定 Asset Key 写 MinIO，元数据事务写 document_assets。
    # Asset 完成前不能进入 READY，答案不会引用尚不存在的图片。
    await self._persist_assets(document, parsed.assets)

    # 补齐检索展示和来源定位字段后，原子覆盖本地 UTF-8 JSON 快照。
    # 快照失败会在调用 Embedding 前停止，避免模型费用和本地审查内容发生分叉。
    chunks = [replace(chunk, metadata={
        **chunk.metadata,
        "filename": document.filename,
        "source_locator": chunk.locator.to_metadata() if chunk.locator else {},
    }) for chunk in chunks]
    await self._chunk_snapshot_store.save(
        document=document,
        parsed_document=parsed,
        parser_name=parser.name,
        parser_version=parser.version,
        chunks=chunks,
    )

    # ── 阶段 4：Embed ─────────────────────────────────────────
    await self._repository.update_document_status(document.id, DocumentStatus.EMBEDDING)
    vectors = await self._embedder.embed_documents([c.content for c in chunks])
    embedded = [EmbeddedChunk(chunk=c, embedding=tuple(v))
                for c, v in zip(chunks, vectors, strict=True)]  # strict：数量必须对齐

    # ── 阶段 5：Index ─────────────────────────────────────────
    await self._repository.update_document_status(document.id, DocumentStatus.INDEXING)
    await self._repository.replace_chunks(document.id, chunks)       # 事务内先删后插
    await self._vector_store.delete_by_document(document.id)          # 幂等重建
    await self._vector_store.upsert(embedded)

    # READY 是提交标志，只能放在最后
    await self._repository.update_document_status(document.id, DocumentStatus.READY)
    return await self._repository.get_document(document.id)
```

### 每个阶段数据如何变化

| 阶段 | 输入 | 处理 | 输出 |
|---|---|---|---|
| Parse | MinIO 原始字节 | 按格式解析、PDF 图片 Vision | `ParsedDocument`（Block + ParsedAsset） |
| Chunk/Asset | Block + Asset | 结构感知切块；图片写 MinIO/PG | `Chunk[] + DocumentAsset[]` |
| Snapshot | 最终 Chunk + Parsed metadata | UTF-8 JSON 临时写入后原子覆盖 | `data/chunk_snapshots/{kb_id}/{document_id}/chunks.json` |
| Embed | Chunk 文本 | 百炼向量化（分批） | `EmbeddedChunk[]`（向量 + 文本） |
| Index | Chunk + 向量 | 写 PostgreSQL + Milvus | 三者一致，可检索 |

### 本地快照保存什么

每个 `chunks.json` 包含版本号、写入阶段、文档/Parser 信息、`ParsedDocument.metadata`，以及
有序的完整 Chunk 列表：`id`、正文、Token 数、标题路径、`SourceLocator` 和最终
`metadata`。它不保存 Embedding，因为向量体积大、可重建且与具体模型绑定。

本地直跑默认写入 `data/chunk_snapshots/`。Docker Compose 将同一路径 bind mount 给 API 与
Worker，所以容器重建后宿主机文件仍在，删除文档/知识库时也能同步清理其中的明文副本。
同一文档重试或重新解析只会原子覆盖稳定的 `chunks.json`，不会无限追加历史文件。

### 状态先于动作

每个阶段开始前**先把状态更新为对应值**。进程中断时，数据库停留在「最后开始的阶段」，便于定位故障；READY 永远在最后，全部成功才算完成。

## 6. 第 5 步：任务完成

```python
await self._repository.complete_ingestion_job(job.id, self._worker_id)
```

`complete_ingestion_job` 校验：

1. 任务仍属于当前 Worker（租约未丢失）
2. 文档确实是 READY

两者都满足才把任务标记 SUCCEEDED，否则抛错（幂等保护）。

## 7. 失败路径：重试 or 终态

处理抛异常时，Worker 调用 `_handle_processing_failure`：

```python
await self._processor.cleanup_partial_index(job.document_id)   # 清理半成品向量

# 只对"很可能是临时故障"的错误重试
retryable = not isinstance(exc, (InvalidDocumentError, ResourceNotFoundError))

# 指数退避，封顶 5 分钟
retry_delay = min(self._retry_delay_seconds * (2 ** max(job.attempts - 1, 0)), 300)

will_retry = await self._repository.fail_ingestion_job(
    job.id, self._worker_id,
    error_message=message, retryable=retryable, retry_delay_seconds=retry_delay)
```

| 错误类型 | 例子 | 是否重试 |
|---|---|---|
| 永久错误 | 不支持格式、文件损坏、空文档 | ❌ 直接 FAILED |
| 临时错误 | 网络超时、本地快照暂时不可写、Embedding 5xx、Milvus 不可用 | ✅ 指数退避（≤3 次） |

重试时任务回到 PENDING 并设置 `available_at`（延迟后重新可见），文档回到 PENDING 并记录「第 N 次处理暂时失败」。

## 8. 前端视角：状态轮询

上传返回 202 后，前端轮询：

```text
GET /knowledge-bases/{kb_id}/documents
→ PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
或 FAILED（含 error_message 供展示）
```

## 9. 一致性要点速查

- **Document + Job 同事务**：无丢任务窗口
- **稳定 Chunk ID**：重试结果一致，无重复
- **稳定 Asset ID/Object Key**：重试覆盖同一图片，不生成随机资源；旧资源在事实替换前清理
- **原子 Chunk 快照**：最终 metadata 补齐后、Embedding 前覆盖；失败不继续调用模型或写索引
- **replace_chunks 先删后插**（事务）：PostgreSQL 不留半套 Chunk
- **delete_by_document + upsert**：Milvus 幂等重建
- **READY 最后**：只有全部成功才可检索
- **READY 过滤**：即使 Worker 崩溃留下半成品向量，检索层也会用 PostgreSQL 状态二次过滤

## 10. Parser 升级后的存量文档

已经 READY 的文档不会因为部署新 Parser 自动改变事实。知识库工作台的“重新解析”按钮调用：

```text
POST /api/documents/{document_id}/reindex → 202 PENDING
```

它复用原 Document ID 和 MinIO 原文件，在数据库行锁内重置唯一 IngestionJob。Worker 随后幂等
替换 Asset、本地 Chunk 快照、PostgreSQL Chunk 和 Milvus 索引；处理中重复提交返回 409，
避免两个 Worker 并发重建。

## 下一步

- 文档状态怎么流转 → [文档状态机](/workflows/state-machine)
- 入库后如何被检索 → [检索问答全流程](/workflows/query)
