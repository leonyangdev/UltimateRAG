# VectorStore：Milvus Dense + BM25

代码位置：`src/ultimate_rag/vectorstores/milvus.py`

## 1. 职责边界

`MilvusVectorStore` 负责两个派生索引的创建、幂等写入、检索和删除，并把 PyMilvus Hit 转换为
项目自己的 `RetrievalResult`。它不决定文档状态，不做 RRF/Rerank，也不保存业务事实。

```python
MilvusVectorStore(
    uri="http://localhost:19530",
    collection="knowledge_chunks",
    sparse_collection="knowledge_chunks_sparse_v3",
    dimension=1024,
    bm25_k1=1.2,
    bm25_b=0.75,
)
```

## 2. 为什么使用两个 Collection

Milvus 不能给已有 Collection 在线增加 BM25 Function。V3 因而保留 V1/V2 Dense Collection，
新增 Sparse Sidecar；历史 Dense 数据不停机，Sparse 可直接从 PostgreSQL Chunk 原文回填，
不必重新支付 Embedding 费用。

| Collection | 向量字段 | 索引 / 度量 | 输入 |
|---|---|---|---|
| `knowledge_chunks` | `FLOAT_VECTOR(1024)` | `AUTOINDEX / COSINE` | 百炼 Embedding |
| `knowledge_chunks_sparse_v3` | `SPARSE_FLOAT_VECTOR` | `SPARSE_INVERTED_INDEX / BM25` | Milvus 本地 Function |

两个 Schema 都关闭 Dynamic Field，并保存稳定 Chunk 主键、知识库/文档 ID、文件名、正文和来源定位。

## 3. 中文 BM25

Sparse `content` 开启 Analyzer：

```python
analyzer_params={"tokenizer": "jieba", "filter": ["lowercase"]}
```

`jieba` 恢复中文词边界，`lowercase` 统一英文大小写并保留数字型号。默认 `k1=1.2`、`b=0.75`、
`DAAT_MAXSCORE` 是基线，不能脱离企业查询集宣称最优。Analyzer 或参数变化时应显式重建 Collection。

## 4. 写入与一致性

```text
delete_by_document（两个 Collection）
        ↓
Dense upsert + flush
        ↓
Sparse 原文 upsert → BM25 Function + flush
        ↓
全部成功后 Application 才把文档置为 READY
```

稳定 Chunk ID 使重试覆盖同一实体。任一写入失败会阻止 `READY`，Worker 补偿会清理两个 Collection
的半成品。`upsert_sparse()` 则只供历史回填使用，不调用 Embedding。

## 5. 检索与过滤

- `search(query_vector, ...)`：COSINE Dense Search
- `search_sparse(query, ...)`：Milvus Analyzer + BM25 Search
- 两者始终下推 `knowledge_base_id`
- 可选 `document_ids` 最多 50 个，并同时下推两个通道
- 不可信值使用 JSON 字符串编码构造过滤表达式，字段与操作符固定在代码中

搜索返回的 `dense_score` 或 `sparse_score` 只是该通道的一次请求内分数。跨通道融合由应用层 RRF
处理，不能直接相加。

## 6. 删除与重建

文档和知识库删除会同步清理 Dense/Sparse 并 Flush；只有全部成功后上层才能返回 204。物理
`row_count` 可能暂含 Tombstone，业务可见性应以强一致 Search/Query 为准。

历史数据回填：

```bash
uv run python scripts/rebuild_sparse_index.py
uv run python scripts/rebuild_sparse_index.py --knowledge-base-id <id> --replace
```

`--replace` 只允许明确知识库，只删除 Sparse 派生行。PostgreSQL 与 Dense Collection 不受影响。

## 7. Async 与替换边界

PyMilvus 是同步 SDK，所有网络调用经 `asyncio.to_thread` 移出 FastAPI Event Loop。替换向量库时
实现领域 `VectorStore` 的 Dense/Sparse 方法并在 Composition Root 换装即可，Application 不接触
SDK Hit、Collection Schema 或 BM25 Function。

## 下一步

- 两个通道怎样融合 → [Retrieval 高级检索](/modules/retrieval)
- Milvus 为什么只是派生索引 → [三大存储职责](/architecture/data-stores)
