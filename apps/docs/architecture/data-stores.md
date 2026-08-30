# 三大存储职责

这是 UltimateRAG 最重要、也最容易理解错的数据边界。**PostgreSQL、MinIO、Milvus 各自保存不同的东西，职责绝不混淆。**

## 1. 一句话职责划分

| 存储 | 保存什么 | 角色定位 |
|---|---|---|
| **PostgreSQL** | 业务**事实**数据 | 事实来源（Source of Truth） |
| **MinIO** | **原始文件** | 对象存储 |
| **Milvus** | **Dense 向量与 BM25 稀疏索引** | 派生索引（Derived Index） |

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
```

**为什么 Chunk 文本也放 PostgreSQL？**

因为 Chunk 是「业务事实」，而向量只是它的「派生索引」。当 Milvus 需要重建时，可以从 PostgreSQL 的 Chunk 重新向量化。同时，检索结果可以用 PostgreSQL 的 `READY` 状态做**二次过滤**，防止半成品向量进入答案。

数据库 Schema 只通过 **Alembic Migration** 修改，应用启动不会自动建表。

## 3. MinIO —— 原始文件

保存**原始文件**（Markdown / PDF / DOCX / XLSX / PPTX / 图片…）。

关键规则：

- **对象键由系统生成**，不直接用用户文件名（防路径穿越、防覆盖）
- 键格式：`{知识库ID}/{文档UUID}/source{扩展名}`
- 先存原文件再建业务记录：即使处理失败，也能用原文件排查或重建
- 用户文件名只用于展示和扩展名判断

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

## 5. 写路径与读路径

### 写入时（Ingestion）

```text
MinIO 先存原文件
   ↓
PostgreSQL 建 Document + Job（同一事务）
   ↓
Worker 处理：
   Parse → Chunk
   ↓
PostgreSQL 写 Chunk 事实（事务）
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

## 6. 删除时的顺序（跨存储一致性）

删除文档/知识库时，按「**派生 → 原文件 → 事实**」的顺序：

```text
1. 先删 Milvus 向量（派生索引）
2. 再删 MinIO 原文件
3. 最后删 PostgreSQL 事实
```

为什么最后删 PostgreSQL？因为前两步失败时，PostgreSQL 事实记录仍然存在，运维可以根据它知道**还要清理哪些外部资源**。这是「明确失败状态 + 补偿操作」思想的体现，V3 不做跨存储事务。

## 7. 为什么不让 Milvus 当事实来源

如果只把业务状态存在 Milvus，会面临：

- Milvus 是**可替换组件**，换向量库 = 丢业务状态
- Milvus 删除后物理行数可能短暂包含 Tombstone（等待 Compaction），不可靠
- 检索之外的业务（状态查询、任务调度）需要关系型查询能力

因此：**业务事实放 PostgreSQL，派生索引放 Milvus。**

## 8. 一张图总结

```text
┌─────────────────────────────┐
│          MinIO              │
│      原始文件（不可变）       │
└───────────┬─────────────────┘
            │ 上传时先保存
            ▼
┌─────────────────────────────┐       重建
│       PostgreSQL            │ ──────────────────► ┌──────────────┐
│  事实：知识库/文档/Chunk/任务 │                       │    Milvus    │
│  （业务状态 + 文本）          │ ◄────────────────── │  向量索引     │
└─────────────────────────────┘   重新向量化        └──────────────┘
```

## 下一步

- 想知道数据库表长什么样 → [Infrastructure 基础设施](/modules/infrastructure)
- 想知道检索怎么用 PostgreSQL 过滤 → [检索问答全流程](/workflows/query)
