# VectorStore 向量库

代码位置：`src/ultimate_rag/vectorstores/milvus.py`

## 1. 这一层是什么

VectorStore 负责向量的**写入、检索和删除**。本项目用 Milvus 实现 `MilvusVectorStore`，它是可替换的派生索引存储。

## 2. 核心类：MilvusVectorStore

```python
MilvusVectorStore(
    uri="http://localhost:19530",
    collection="knowledge_chunks",   # Collection 名
    dimension=1024,                  # 向量维度（必须与 Embedder 一致）
)
```

## 3. 重要前提：这是「派生索引」

Milvus **不是业务事实来源**。它只保存可由 PostgreSQL Chunk + MinIO 原文件重建的派生索引。这意味着：

- Collection 丢了可以重建（重新向量化即可）
- 业务状态（文档状态、任务状态）绝不放 Milvus
- 检索结果会用 PostgreSQL `READY` 状态二次过滤

## 4. Schema 设计

Collection `knowledge_chunks` 的字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR（主键） | Chunk 稳定 ID（非 Milvus 自动 ID） |
| `knowledge_base_id` | VARCHAR | 检索隔离边界 |
| `document_id` | VARCHAR | 文档归属 |
| `chunk_id` | VARCHAR | Chunk 标识 |
| `filename` | VARCHAR | 展示用文件名（随向量保存，避免 N+1 查库） |
| `content` | VARCHAR | Chunk 文本 |
| `heading_path` | JSON | 来源定位（兼容 V1 数组和 V2 Locator 字典） |
| `embedding` | FLOAT_VECTOR(1024) | 向量 |

关键设计：

- **关闭 Dynamic Field**：字段拼写/类型错误尽早失败
- **主键用应用生成的稳定 Chunk ID**：摄取重试才能覆盖同一实体（幂等）
- **COSINE 距离** + **AUTOINDEX**（把物理索引选择交给 Milvus）
- **Strong 一致性**：保证 READY 后立即可检索、删除后立即不可见

## 5. 关键方法

### ensure_collection —— 幂等创建

```python
async def ensure_collection(self):
    await asyncio.to_thread(self._ensure_collection_sync)

def _ensure_collection_sync(self):
    if self._client.has_collection(self._collection):
        return            # 已存在直接复用
    # 定义 Schema + COSINE 索引 + Strong 一致性，创建 Collection
    ...
```

- 在进程启动时调用，幂等
- 已存在则复用，**不隐式修改已有 Schema**

### upsert —— 幂等写入

```python
async def upsert(self, chunks):
    if not chunks:
        return
    rows = [{
        "id": item.chunk.id,
        "knowledge_base_id": item.chunk.knowledge_base_id,
        "filename": item.chunk.metadata.get("filename", ""),
        "content": item.chunk.content,
        "heading_path": item.chunk.locator.to_metadata() if item.chunk.locator else ...,
        "embedding": list(item.embedding),
    } for item in chunks]
    await asyncio.to_thread(self._upsert_sync, rows)

def _upsert_sync(self, rows):
    self._client.upsert(collection_name=self._collection, data=rows)
    self._client.flush(collection_name=self._collection)   # 落盘后才返回
```

- 主键是稳定 Chunk ID → Upsert 幂等，重试不会重复
- **写入后 Flush**：只有落盘成功才返回，上层才能标记 READY
- 同步 SDK 全部 `asyncio.to_thread`，不阻塞事件循环

### search —— 知识库范围内检索

```python
async def search(self, query_vector, knowledge_base_id, top_k):
    result = await asyncio.to_thread(
        self._client.search,
        collection_name=self._collection,
        data=[list(query_vector)],
        filter=f'knowledge_base_id == "{knowledge_base_id}"',   # 隔离边界
        limit=top_k,
        output_fields=["knowledge_base_id", "document_id", "chunk_id",
                       "filename", "content", "heading_path"],
    )
    hits = result[0] if result else []
    return [self._retrieval_result(hit) for hit in hits]
```

- **知识库过滤在 Milvus 层完成**，不是全库召回再过滤
- 每个 Hit 转换为领域对象 `RetrievalResult`，SDK 结构不泄漏到上层

### 删除

```python
async def delete_by_document(self, document_id):
    await asyncio.to_thread(self._delete_sync, f'document_id == "{document_id}"')

async def delete_by_knowledge_base(self, knowledge_base_id):
    await asyncio.to_thread(self._delete_sync, f'knowledge_base_id == "{knowledge_base_id}"')
```

- 删除后也 Flush，保证 204 响应意味着向量已持久化清理

## 6. 常见坑：Tombstone

::: tip NOTE
Milvus 删除后物理 `row_count` 可能暂时包含等待 Compaction 的 Tombstone。
业务可见性必须使用强一致 Query/Search 判断，不能把物理行数当作有效 Chunk 数。
:::

## 7. 为什么同步 SDK 要 to_thread

PyMilvus 是同步接口。直接在主线程调用会阻塞 FastAPI 事件循环（它承担着所有并发请求）。因此所有网络调用都通过 `asyncio.to_thread` 移到工作线程。

## 8. 换向量库要做什么

1. 新建一个类实现 `VectorStore` 端口（`ensure_collection` / `upsert` / `search` / `delete_*`）
2. 在 `runtime.py` 替换 `MilvusVectorStore` 为你的实现

检索、上下文、生成全都不用改。

## 下一步

- 向量检索出来后怎么拼上下文 → [Generation 生成](/modules/generation)
- 或者看检索全流程 → [检索问答全流程](/workflows/query)
