# ADR-002：V3 Hybrid Retrieval 与无损索引升级

## Status

Accepted，2026-08-30。

## Context / 背景

V2 只有 `text-embedding-v4 + Milvus COSINE`。语义相近的问题通常可以召回，但型号、版本号、
缩写、表格字段等精确词容易漏召；同时 Dense 原始分数不能直接与 BM25 分数相加。V3 产品范围要求
Dense、Sparse、Hybrid、RRF、Reranker、Query Rewrite、Metadata Filtering 和 Parent-Child。

已有生产边界不能被破坏：

- PostgreSQL Chunk 与 MinIO 原文是事实，Milvus 是可重建派生索引。
- 现有 `knowledge_chunks` 已有 V1/V2 数据，Milvus 不能在线给它增加 BM25 Function 字段。
- 历史文档不应仅为 Schema 升级再次调用计费 Embedding。
- 高级辅助模型失败时，基础检索应尽可能继续，但不能静默假装完整链路已执行。

## Decision / 决策

### 1. 保留 Dense Collection，新增 Sparse Sidecar Collection

`knowledge_chunks` 继续保存 V2 Dense 向量；新增 `knowledge_chunks_sparse_v3`，保存相同稳定 Chunk ID、
原文和来源定位，由 Milvus BM25 Function 本地生成 `SPARSE_FLOAT_VECTOR`。新文档只有两个索引都写入
成功后才能进入 `READY`。历史数据使用 `scripts/rebuild_sparse_index.py` 从 PostgreSQL 幂等回填，
不重新生成 Embedding。

### 2. BM25 使用 Milvus 本地 Function

中文技术文档采用 `jieba + lowercase`：Jieba 恢复中文词边界，lowercase 统一英文大小写但保留
字母数字型号。索引使用 `SPARSE_INVERTED_INDEX / BM25 / DAAT_MAXSCORE`。`k1=1.2`、`b=0.75`
采用 Milvus 默认基线，后续只能依据真实评估集调整。

### 3. 在 Application 层做 RRF

每个查询变体分别获得 Dense/BM25 排名，应用层按
`score(d) = Σ 1 / (60 + rank_i(d))` 融合。选择应用层而不是 SDK 内置 Hybrid Ranker，是为了：

- 领域服务不绑定 Milvus Hybrid API；
- 保留每个通道原始分数和来源标签；
- 纯函数可以独立单测；
- Dense 与 Sparse 分集合仍能统一融合。

RRF 常数 60 来自原始论文，是基线而非不可修改的真理。

### 4. 原查询 + 至多一个保守改写

`qwen-plus` 使用 JSON Object 输出一个查询变体。原查询始终保留，改写必须保留型号、数字、日期和
专有名词。模型输出经过 Pydantic 校验；失败或返回相同文本时继续使用原查询。V3 不做 Query
Decomposition、Agent Loop 或多轮自我纠错，这些属于 V6。

### 5. 有限候选后使用百炼 Reranker

默认先广召回 30 个唯一候选，再由 `qwen3-rerank` 一次批量重排，最终只保留 API `top_k`
（默认 5）。不继续采用 `gte-rerank-v2`，因为阿里云已在 2026-05-30 停止该模型。Adapter 按
官方总量公式控制请求并为 tokenizer 近似预留 10% 余量；预算不足时保留 RRF 排名前缀，不能静默
截断 Query。Rerank 分数只表示本次 Query/候选集中的相对相关性，不能作为跨请求概率阈值。
Reranker 失败时回退到 RRF 顺序，并在 Retrieval Trace 中记录 `rerank_failed`。

### 6. Metadata Filter 先由 PostgreSQL 求事实交集，再下推 Milvus

V3 对外提供最多 50 个 `document_ids` 白名单；`knowledge_base_id` 始终强制存在。请求 ID 先与
PostgreSQL 中当前知识库的 `READY` 文档求交，再同时下推 Dense/Sparse Search。即使没有显式过滤，
Hit 仍按 PostgreSQL `READY` 状态二次过滤，弥合跨存储非原子窗口。

### 7. Parent-Child 使用有界 Small2Big

Chunker 为同一语义 Section 的 Child 写入稳定 `parent_id`。检索命中小 Child，最终上下文只扩展
同 Parent 的前后各一个 Child，默认总预算 1536 Token。历史 V2 Chunk 没有 Parent ID 时，严格按
同文档、标题路径和页/Sheet/Slide 边界回退。数据库用两条批量查询读取所有命中及邻居，不产生 N+1。

## Alternatives / 备选方案

### 直接删除并重建统一 V3 Collection

Schema 最整齐，但升级期间会让历史 `READY` 文档暂时不可检索，并再次产生全部 Embedding 费用。
在没有蓝绿索引切换机制的当前版本中不采用。

### Elasticsearch/OpenSearch 负责 BM25

全文检索能力成熟，但新增一套集群、同步链路和运维面，当前 Milvus 2.5 已满足本版本需求。

### 直接加权 COSINE 与 BM25 原始分数

两类分数尺度不同，需要针对每个数据集校准权重；没有评估数据时权重只是猜测，因此采用 RRF。

### 用 LLM 对所有候选逐条判断

延迟、费用和非确定性明显更高，也无法替代可独立测试的召回层，不采用。

## Consequences / 影响

收益：

- 历史 Dense 索引零停机保留，Sparse 回填不产生模型费用。
- 精确词与语义召回互补，每阶段可解释、可消融、可评估。
- 单个辅助模型或 Hybrid 单通道临时失败时仍有明确降级路径。
- Domain 不依赖 Milvus、OpenAI SDK 或 LangChain 数据模型。

成本与限制：

- 两个 Milvus Collection 增加写入与删除补偿面；文档只有两者成功后才 `READY`。
- 首次升级必须显式运行 Sparse 回填工具，否则历史文档只有 Dense 通道可命中。
- Query Rewrite 和 Rerank 增加外部延迟与百炼用量，应通过评估决定是否按场景关闭。
- 当前 Metadata Filter 只开放知识库和文档 ID，不包含 ACL；权限与多租户属于 V4。
- 当前离线指标工具不是 V5 的 Golden Dataset 管理、在线 Trace 或回归平台。

## Primary references / 一手资料

- [Milvus BM25 Function](https://milvus.io/docs/bm25-function.md)
- [Milvus Full Text Search](https://milvus.io/docs/full-text-search.md)
- [Milvus Analyzer 选择](https://milvus.io/docs/choose-the-right-analyzer-for-your-use-case.md)
- [Reciprocal Rank Fusion 原始论文（SIGIR 2009）](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)
- [Microsoft RAG Information Retrieval](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-information-retrieval)
- [阿里云百炼 Text Rerank API](https://help.aliyun.com/en/model-studio/text-rerank-api)
- [阿里云百炼 Qwen 结构化输出](https://help.aliyun.com/en/model-studio/qwen-structured-output)
