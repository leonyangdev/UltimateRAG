# V3 能力与限制

V3.0 的主题是 **Advanced Retrieval**：保留 V2 的文档智能摄取，把单路向量检索升级为可解释、
可降级、可离线评估的多阶段检索。

## 1. 用户能做什么

- 在 Dense、Sparse、Hybrid 三种模式间切换
- 开关 Query Rewrite、Reranker 和 Small2Big 上下文扩展
- 只在勾选的文档内检索，最多 50 份
- 在检索调试页查看查询变体、候选数、降级原因及每阶段分数
- 在聊天证据卡中查看相同 Trace，不为展示信息重复检索
- 在答案正文显示 PDF 图片和 GFM 表格，点击来源链接打开右侧证据栏
- 恢复历史会话时继续查看当轮 Citation、检索 Trace 和图片 Asset
- 删除任意历史会话；删除当前会话后自动恢复最近会话，没有历史时回到未持久化 Draft
- 使用 JSONL 标注集比较 Precision@k、Recall@k、MRR@k、nDCG@k

## 2. 默认查询链路

```text
READY / 文档白名单过滤
        ↓
原查询 + 至多一个保守改写
        ↓
Dense(COSINE)  +  Sparse(BM25)
        ↓
RRF(k=60)，默认保留 30 个候选
        ↓
qwen3-rerank，收敛到 top_k
        ↓
同 Parent 的相邻 Child，默认总计不超过 1536 Token
```

原查询始终保留。COSINE 与 BM25 的原始分数不直接相加；RRF 只使用名次完成融合。命中的
Child ID 也始终保留，扩展只改变提供给生成模型的上下文。

## 3. 哪些能力在线，哪些能力本地

| 能力 | 实现 | 是否调用线上模型 |
|---|---|---|
| Dense Query Embedding | 百炼 `text-embedding-v4` | 是 |
| Query Rewrite | 百炼 `qwen-plus` 结构化输出 | 是，可关闭 |
| Sparse 分词与 BM25 | Milvus `jieba + lowercase` / BM25 Function | 否，本地 Docker |
| RRF | UltimateRAG 纯 Python | 否 |
| Reranker | 百炼 `qwen3-rerank` | 是，可关闭 |
| Metadata Filter | PostgreSQL + Milvus 标量过滤 | 否 |
| Small2Big | PostgreSQL 批量读取相邻 Chunk | 否 |

## 4. 可解释与降级

`POST /api/retrieval/explain` 返回 `results + trace`。结果会保留 `dense_score`、`sparse_score`、
`fusion_score`、`rerank_score` 和 `retrieval_sources`；Trace 会说明改写、重排、上下文扩展是否
真正执行，以及是否发生 `dense_retrieval_failed` 等降级。

Hybrid 某一路临时失败时使用另一路；Rewrite、Rerank 或 Parent 扩展失败时保留上一阶段结果；
所有请求的召回通道都失败时请求失败，不能伪装成“没有相关内容”。

## 5. 历史 V1/V2 数据升级

V3 保留原 `knowledge_chunks` Dense Collection，并新增 `knowledge_chunks_sparse_v3`。历史 Chunk
从 PostgreSQL 回填 Sparse 索引，不重新调用计费 Embedding：

```bash
uv run python scripts/rebuild_sparse_index.py
```

应用启动不会偷偷迁移大量索引。修改 Analyzer 或 BM25 参数后也应显式重建。

PDF Parser 升级后的存量 READY 文档还需要在知识库工作台点击“重新解析”，或调用
`POST /api/documents/{id}/reindex`。该操作复用 MinIO 原文件并由 Worker 后台回填图片 Asset、
新 Chunk 与向量，不要求重新上传。

## 6. 多模态回答与来源边界

图片不是作为 Base64 塞入 Prompt，而是使用 `asset://稳定ID`。二进制在 MinIO，元数据在
PostgreSQL，检索完成后按 Chunk 批量关联；前端只有在本消息证据白名单内才加载对应 API。
表格继续使用 Markdown 源数据。`[来源 N](citation://N)` 点击后打开右侧侧栏，展示精确 Locator、
检索文本、图片或 PDF BBox 预览。来源编号必须存在于当前消息的 Citation 快照；组合历史引用会
拆成独立链接，越界编号不会回退到另一个 RetrievalResult。桌面来源栏从右侧展开，移动端带遮罩，
两者都支持 Escape 和标准焦点管理。

聊天页面把“新对话”作为未持久化 Draft：进入知识库或点击“新建对话”只切换本地视图，已经处于
Draft 时重复点击不会创建新记录；首次发送 Chat 问题前才持久化会话并加入历史列表。桌面会话栏
可折叠成保留核心操作的 64px 图标轨；删除使用知识库作用域 API 和二次确认，生成中的当前会话由
服务端 409 保护。删除最后一条历史会话后回到 Draft，不创建替代空会话。

## 7. V3 的边界

- 文档过滤是业务筛选，不是 ACL；身份、组织、权限与审计属于 V4
- JSONL 指标工具用于 V3 调参，不是 V5 Golden Dataset/RAGOps 平台
- 不做 Query Decomposition、Agent Loop、GraphRAG 或 Tool Calling
- 默认参数只是公开实践基线，必须用企业自己的文档和查询集验证

完整设计见仓库的 `docs/5.v3_implementation.md` 和 `docs/adr/ADR-002-v3-hybrid-retrieval.md`。

## 下一步

- 深入每个阶段的代码边界 → [Retrieval 高级检索](/modules/retrieval)
- 跟踪一次请求 → [检索问答全流程](/workflows/query)
- 查看请求与 Trace 格式 → [REST API 参考](/reference/api)
