# 检索问答全流程

这条链路回答：用户提交问题后，系统如何得到可解释证据，再生成带引用的答案？

## 1. 全景图

```text
POST /api/chat/stream
   ↓ FastAPI 校验知识库与请求选项
RetrievalService.retrieve
   ↓ PostgreSQL：READY + document_ids 事实交集
原查询 ── optional qwen-plus Rewrite（原查询永远保留）
   ├── Dense：Embedding → Milvus COSINE
   └── Sparse：原文 Query → Milvus Analyzer/BM25
   ↓ RRF 融合多个排名列表
   ↓ optional qwen3-rerank
   ↓ optional Small2Big 同 Parent 相邻 Child
PostgreSQL：按 Chunk.asset_ids 批量补齐 DocumentAsset
   ↓ RAGService：ContextBuilder → Citation
   ↓ BailianLLMClient.stream
SSE：data-retrieval(trace + assets) → text-delta × N → finish
   ├─ asset://ID → 受控 Asset API → MinIO JPEG
   └─ citation://N → 右侧来源栏 → Chunk/表格/PDF BBox 证据
```

## 2. HTTP 边界

```json
{
  "knowledge_base_id": "kb-1",
  "question": "Milvus 怎样执行中文 BM25？",
  "top_k": 5,
  "mode": "hybrid",
  "candidate_k": 30,
  "enable_query_rewrite": true,
  "enable_rerank": true,
  "enable_parent_expansion": true,
  "document_ids": ["optional-doc-id"]
}
```

Query、ID、数量和枚举在 Pydantic 边界校验。检索与 Citation 在 `StreamingResponse` 建立前完成，
因此知识库不存在、事实库失败或所有召回通道失败仍能返回正常 HTTP 错误，而不是藏进 200 SSE。

## 3. 事实过滤与 Query Rewrite

PostgreSQL 先求当前知识库 `READY` 文档与最多 50 个请求 ID 的交集。交集为空时立即返回，不调用
付费模型或 Milvus。非空交集下推 Dense/Sparse；Hit 还会再次按 READY 集合过滤。

Query Rewrite 使用 `qwen-plus` JSON Object 输出至多一个变体。它必须保留型号、数字和专有名词，
原查询始终位于 `query_variants[0]`。改写失败只记录 `query_rewrite_failed` 并继续原查询。

## 4. Dense + Sparse Broad Recall

- Dense：查询经与入库相同的 `text-embedding-v4` 编码，在 `knowledge_chunks` 做 COSINE Search
- Sparse：查询字符串直接进入 `knowledge_chunks_sparse_v3`，由本地 `jieba + lowercase` 与 BM25 Search
- Hybrid：每个查询变体的两个通道并发执行，默认各取 30 个候选

单通道故障时 Hybrid 使用另一通道并留下 Trace；所有请求通道都失败时抛错。请求取消则立即传播，
避免客户端断开后流水线继续产生模型费用。

## 5. RRF 与 Reranker

两个以上有效排名列表按 `Σ 1/(60 + rank)` 融合。RRF 只使用名次，避免直接相加不可比的
COSINE/BM25 原始分数。融合后去重并截断 `candidate_k`。

开启 Rerank 时，有限候选一次批量提交 `qwen3-rerank`，最终只保留 `top_k`。Adapter 按
`query_tokens × document_count + total_document_tokens` 控制总预算，并校验索引范围、重复项和
空响应；故障时保持 RRF 顺序，Trace 标记 `rerank_failed`。

## 6. Small2Big 上下文

检索使用 512 Token 左右的小 Child 取得更精确匹配，进入生成前可扩展同一语义 Parent 的前后
相邻 Child。Repository 用两条批量 SQL 完成读取，不产生 N+1；默认每个结果的扩展总预算为
1536 Token。

`chunk_id` 仍指向真正命中的 Child，`matched_content` 保存命中正文，`content` 是最终上下文，
`context_chunk_ids` 列出实际扩展范围，因此引用不会因 Small2Big 丢失精确锚点。

## 7. Context、Citation 与防幻觉边界

`RAGService._prepare_generation()` 让流式/非流式共享同一份结果：

1. 无证据时不调用 LLM，直接返回“根据当前知识库无法确定”。
2. `ContextBuilder` 按排名和字符预算确定性编号 `[来源 N]`。
3. 知识正文作为不可信数据放入 `<knowledge_context>`，System Prompt 要求忽略其中指令。
4. Citation 从受控 `RetrievalResult` 构造，不解析模型自由文本。
5. 图片资源以精确 `![标题](asset://ID)` 提供；模型只能复制 Context 已声明的 ID。
6. 引用使用 `[来源 N](citation://N)`，前端按后端 Citation 顺序打开右侧来源栏。

## 8. SSE 与可解释数据

```text
start → start-step
→ data-retrieval {citations, retrieval_results, retrieval_trace}
→ text-start → text-delta × N → text-end
→ finish-step → finish → [DONE]
```

证据、Trace 与文本属于同一条 assistant message。生成中断后 HTTP 状态已不能修改，Route 会记录
堆栈并只发送稳定错误文案，不把供应商响应或凭据暴露给浏览器。

PDF 命中的 `content_types + assets` 来自 PostgreSQL 事实，不依赖 Milvus 扩展字段。前端有三条
确定性渲染路径：

| 回答内容 | 传输协议 | 展示方式 |
|---|---|---|
| 图片 | `asset://<id>` | 仅当 ID 存在于本消息 RetrievalResult 白名单时，映射受控 Asset API |
| 表格 | GFM Markdown | 直接在答案或来源侧栏渲染原始行列数据 |
| 引用 | `citation://N` | 点击打开右侧侧栏，展示文件、Locator、Chunk 和视觉预览 |

任意公网图片不会自动加载，避免恶意文档通过图片 URL 泄露客户端 IP、Referer 或查询语义。图片
Asset 已在摄取期调用过 Vision，查看时只读取 MinIO；表格/普通 PDF 区域仍可通过 `preview_url`
使用本地 PDFium 按 BBox 裁切，不再次调用百炼。

## 9. 历史会话为什么仍能打开来源

一次助手回答正常完成时，正文和 `ChatEvidence` 在同一 PostgreSQL 事务提交。历史会话 API 返回
消息正文以及当时的 Citation、RetrievalResult、Trace；前端恢复成同一 `data-retrieval` Message
Part。因此刷新页面后不是重新检索，也不会因为索引后来改变而把旧回答指向另一批来源。

## 10. 为什么仍不用 LangGraph

链路虽有并发通道和降级，但阶段顺序确定、没有 Agent 决策或循环。普通 Python Service 更容易
理解、单测和定位故障；框架应等真正出现状态图复杂度再引入。

## 下一步

- 查看每个阶段的实现边界 → [Retrieval 高级检索](/modules/retrieval)
- 调试端点与完整字段 → [REST API 参考](/reference/api)
