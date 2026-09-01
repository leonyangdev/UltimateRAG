# 三大核心存储与本地 Chunk 快照

这是 UltimateRAG 最重要、也最容易理解错的数据边界。**PostgreSQL、MinIO、Milvus 各自保存
不同的核心数据；本地 JSON 只提供可读诊断快照，不能升级成第四个事实来源。**

## 1. 一句话职责划分

| 存储 | 保存什么 | 角色定位 |
|---|---|---|
| **PostgreSQL** | 业务**事实**数据 | 事实来源（Source of Truth） |
| **MinIO** | **原始文件 + 抽取图片 Asset** | 对象存储 |
| **Milvus** | **Dense 向量与 BM25 稀疏索引** | 派生索引（Derived Index） |
| **本地 JSON** | Embedding 前的最终 Chunk + metadata | 可重建诊断/审计副本 |

```text
事实数据 + 原文件
   = 可以重建一切（包括向量索引）
```

> 核心推论：**理论上 MinIO + PostgreSQL 就可以重建 Milvus 索引**。Milvus 丢了，重新跑一遍摄取即可；业务状态绝不能只保存在 Milvus。

## 2. PostgreSQL —— 业务事实

保存**业务事实数据和元数据**：

```text
knowledge_bases   知识库
documents         文档元数据 + 处理状态（PENDING/READY/FAILED）
ingestion_jobs    后台任务（领取/重试/完成）
chunks            Chunk 事实（文本 + 元数据 + 来源定位）
document_assets   图片资源事实（Block/Locator/Object Key/SHA-256）
chat_sessions     会话与递归摘要游标
chat_messages     完整消息 + 回答时检索证据 JSONB 快照
```

**为什么 Chunk 文本也放 PostgreSQL？**

因为 Chunk 是「业务事实」，而向量只是它的「派生索引」。当 Milvus 需要重建时，可以从 PostgreSQL 的 Chunk 重新向量化。同时，检索结果可以用 PostgreSQL 的 `READY` 状态做**二次过滤**，防止半成品向量进入答案。

数据库 Schema 只通过 **Alembic Migration** 修改，应用启动不会自动建表。

## 3. MinIO —— 原始文件与二进制 Asset

保存原始文件（Markdown / PDF / DOCX / XLSX / PPTX / 图片…）以及 PDF Parser 已抽取的 JPEG。

关键规则：

- **对象键由系统生成**，不直接用用户文件名（防路径穿越、防覆盖）
- 键格式：`{知识库ID}/{文档UUID}/source{扩展名}`
- Asset 键格式：`{知识库ID}/{文档UUID}/assets/{稳定AssetID}.jpg`
- 先存原文件再建业务记录：即使处理失败，也能用原文件排查或重建
- 用户文件名只用于展示和扩展名判断

原文件用于重建一切；Asset 是为了让答案和历史会话低延迟稳定展示的二进制事实。它仍然可以由
原 PDF + Parser 重建，但不能放进 PostgreSQL JSONB 或 Milvus，避免数据库膨胀和检索索引污染。

## 4. Milvus —— Dense + Sparse 派生索引

只保存**可重建的向量索引和检索数据**，不保存业务状态。

V3 使用两个可独立重建的 Collection：

- `knowledge_chunks`：V1/V2 延续的 Dense COSINE 索引
- `knowledge_chunks_sparse_v3`：原文经 `jieba + lowercase` 分析后，由 Milvus BM25 Function 本地生成的 Sparse 索引

两者共享的核心字段：

```text
id                 主键（Chunk 稳定 ID，幂等写入）
knowledge_base_id  检索隔离边界
document_id / chunk_id
filename / content / heading_path   检索展示与 Citation
embedding / sparse_embedding   Dense FloatVector 或 BM25 SparseFloatVector
```

Dense 使用 `AUTOINDEX + COSINE`；Sparse 使用 `SPARSE_INVERTED_INDEX + BM25`。两者均使用
`Strong` 一致性，保证 READY 后立即可检索、删除后立即不可见。

## 5. 本地 JSON —— Embedding 前的可读快照

Worker 在 Asset 已持久化、Chunk 已补齐 `filename/source_locator` 之后，先写：

```text
data/chunk_snapshots/{knowledge_base_id}/{document_id}/chunks.json
```

快照保存文档与 Parser 信息、`ParsedDocument.metadata`，以及每个 Chunk 的完整正文、标题路径、
Token 数、Locator 和 metadata；不保存 Embedding。它使用 UTF-8、格式化 JSON 和同目录
`os.replace` 原子覆盖。同一文档重试/重建不会追加无界历史，读者也不会看到半截文件。

这份文件包含知识库明文，因此：

- `/data/chunk_snapshots/` 被 Git 忽略；
- Docker Compose 使用 bind mount，让 Worker 与 API 访问宿主机同一目录；
- 文档或知识库删除时同步清理快照；
- 快照写入失败会在 Embedding 前中止，并交给 Worker 有限重试；
- 它不参与检索，也不能替代 PostgreSQL Chunk 事实。

## 6. 写路径与读路径

### 写入时（Ingestion）

```text
MinIO 先存原文件
   ↓
PostgreSQL 建 Document + Job（同一事务）
   ↓
Worker 处理：
   Parse → Chunk + 图片 Asset
   ↓
MinIO 写 Asset；本地原子保存最终 Chunk JSON
   ↓
百炼生成 Dense Embedding
   ↓
PostgreSQL 事务替换 Chunk 事实
   ↓
Milvus 顺序写 Dense 与 Sparse（delete_by_document + upsert + flush）
   ↓
PostgreSQL 标记 READY（最后一步）
```

### 读取时（Query）

```text
用户提问 → 可选改写 → Dense 与 BM25 并发召回（限定知识库/文档）
   ↓
RRF 融合 → Rerank → Small2Big
   ↓
PostgreSQL 二次过滤（只留 READY 文档）
   ↓
拼上下文 → LLM → 答案
```

## 7. 删除时的顺序（跨存储一致性）

删除文档/知识库时，按「**派生 → 原文件 → 事实**」的顺序：

```text
1. 先删 Milvus 向量（派生索引）
2. 再删本地 Chunk 明文快照
3. 再删 MinIO Asset 和原文件
4. 最后删 PostgreSQL 事实
```

为什么最后删 PostgreSQL？因为任一前置清理步骤失败时，PostgreSQL 事实记录仍然存在，运维可以
根据它知道**还要清理哪些外部资源**。这是「明确失败状态 + 补偿操作」思想的体现，V3 不做
跨存储事务。

## 8. 为什么不让 Milvus 当事实来源

如果只把业务状态存在 Milvus，会面临：

- Milvus 是**可替换组件**，换向量库 = 丢业务状态
- Milvus 删除后物理行数可能短暂包含 Tombstone（等待 Compaction），不可靠
- 检索之外的业务（状态查询、任务调度）需要关系型查询能力

因此：**业务事实放 PostgreSQL，派生索引放 Milvus。**

## 9. 一张图总结

```text
┌─────────────────────────────┐
│          MinIO              │
│   原始文件 + 图片 Asset       │
└───────────┬─────────────────┘
            │ 上传时先保存
            ▼
┌─────────────────────────────┐       重建
│       PostgreSQL            │ ──────────────────► ┌──────────────┐
│  事实：文档/Chunk/Asset/会话   │                       │    Milvus    │
│  （业务状态 + 文本）          │ ◄────────────────── │  向量索引     │
└─────────────────────────────┘   重新向量化        └──────────────┘
            │
            └── Embedding 前导出 ──► data/chunk_snapshots/*.json
                                      （可读诊断副本）
```

## 下一步

- 想知道数据库表长什么样 → [Infrastructure 基础设施](/modules/infrastructure)
- 想知道检索怎么用 PostgreSQL 过滤 → [检索问答全流程](/workflows/query)
