# Retrieval 高级检索

核心代码：`application/retrieval.py`、`retrieval/fusion.py`、`retrieval/bailian.py`、
`vectorstores/milvus.py`。

## 1. 模块边界

`RetrievalService` 只负责选证据，不构造最终 Prompt，也不生成答案。它依赖项目自己的
`Embedder`、`VectorStore`、`QueryRewriter`、`Reranker` 端口和 PostgreSQL Repository，
因此 RRF、过滤与降级可以脱离 LLM 单独测试。

```text
RetrievalService
  ├── Repository          READY 事实、文档交集、相邻 Chunk
  ├── Embedder            Dense Query Vector
  ├── VectorStore         Dense Search + Sparse Search
  ├── QueryRewriter       至多一个查询变体
  ├── reciprocal_rank_fusion
  └── Reranker            有限候选批量重排
```

## 2. 召回与融合

每个查询变体会按模式建立独立任务；Hybrid 默认并发执行 Dense 与 Sparse。多个有序列表按下式
融合，默认 `k=60`：

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

RRF 不混加不同尺度的 COSINE/BM25 分数。纯函数会去重、稳定排序，并保留每个 Chunk 来自
`dense:original`、`sparse:rewrite` 等哪些列表。只有一个有效列表时保留该通道原始分数。

## 3. 查询改写与重排

`BailianQueryRewriter` 要求 `qwen-plus` 返回经过 Pydantic 校验的 JSON Object。改写只补充原查询，
不能取代它，也不会生成无界数量的变体。

融合后最多将 `candidate_k` 个正文一次提交给 `qwen3-rerank`。Adapter 检查响应索引范围、重复项
和空结果，并在官方总 Token 上限内保留 RRF 排名前缀；重排分数只用于本次候选集排序，不应当作
全局概率阈值。超长 Query 不会被静默截断，重排失败时应用保留融合顺序。

## 4. Metadata Filter

`knowledge_base_id` 永远下推到 Milvus。可选 `document_ids` 先与 PostgreSQL 中当前知识库的
`READY` 文档求交，再下推两个 Collection；所有 Hit 还会按事实集合二次过滤。这能避免跨库检索，
也阻止跨存储非原子窗口中的半成品索引参与回答。

## 5. Small2Big

Chunker 为同一语义 Section 的 Child 写入稳定 `parent_id`。命中后 Repository 用两次批量 SQL
读取所有目标及相邻位置，应用层再限制：

- 同一文档、同一 Parent
- 默认前后各一个 Child
- 默认总预算 1536 Token
- Citation 的 `chunk_id` 仍指向真实命中，`context_chunk_ids` 记录扩展范围

旧数据没有 Parent ID 时，仅在标题路径及页码、Sheet/Range、Slide 等来源边界完全一致时扩展。

## 6. 故障语义

| 失败阶段 | 行为 |
|---|---|
| Rewrite | 原查询继续，Trace 标记失败 |
| Hybrid 单通道 | 另一通道继续 |
| 所有召回通道 | 抛出错误 |
| Reranker | 保留融合顺序 |
| Parent 扩展 | 保留命中 Child |
| 请求取消 | 立即传播取消，不当作降级 |

`/api/retrieval/search` 为兼容旧客户端仍返回数组；`/api/retrieval/explain`、Chat JSON 和 Chat SSE
会额外返回 `RetrievalTrace`。

## 7. 如何验证参数

```bash
uv run python scripts/evaluate_retrieval.py eval.jsonl --mode hybrid --k 5
uv run python scripts/evaluate_retrieval.py eval.jsonl --mode dense --disable-rerank
```

同一标注集应分别跑 Dense、Sparse、Hybrid 及关闭 Rewrite/Rerank 的消融实验；只有真实指标和延迟、
费用共同支持时才调整默认候选宽度、RRF 常数或线上模型开关。Small2Big 不改变 Chunk 排名指标，
评估脚本默认关闭；需要连同上下文数据库读取一起验证时使用 `--enable-parent-expansion`。
