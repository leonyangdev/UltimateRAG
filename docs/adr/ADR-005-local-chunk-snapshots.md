# ADR-005：在 Embedding 前保存本地 Chunk JSON 快照

## Status

Accepted

## Context

PostgreSQL 已保存最终 Chunk 事实，Milvus 保存可重建索引，但排查解析、切块或 metadata 问题时，
开发者仍需要直接查看“进入 Embedding 和索引的确切输入”。只从 Milvus 读取会混入向量库 Schema
裁剪和序列化细节；只查 PostgreSQL 又无法证明问题发生前是否已经形成预期的 Parser metadata。

快照包含文档明文，因此还必须处理路径穿越、半写文件、Worker 重试、Docker 容器重建和删除后的
数据残留，不能把一次普通 `open(..., "w")` 当作完整实现。

## Decision

1. 在 Asset 持久化、Chunk 补齐 `filename/source_locator` 之后，Embedding 之前保存快照。
2. 使用 `ChunkSnapshotStore` 领域端口隔离应用层与本地文件系统；V3 实现为
   `LocalChunkSnapshotStore`。
3. 稳定路径为：

   ```text
   data/chunk_snapshots/{knowledge_base_id}/{document_id}/chunks.json
   ```

   路径只使用系统 ID，不使用上传文件名。
4. JSON 使用 UTF-8，保存版本号、阶段、文档/Parser 信息、`ParsedDocument.metadata` 和完整
   Chunk/Locator/metadata；不保存 Embedding。
5. 临时文件写完并关闭后使用同目录 `os.replace` 原子覆盖。reindex 和任务重试复用同一路径。
6. 快照采用 fail-closed：写入失败时不调用 Embedding、不写 PostgreSQL Chunk 或 Milvus，并交给
   Worker 的有限重试策略。
7. Docker Compose 将快照目录 bind mount 给 API 与 Worker。目录由 Git 忽略；删除文档或知识库
   时，在删除 PostgreSQL 事实前同步清理对应明文快照。

## Alternatives

### 只依赖 PostgreSQL Chunk 表

优点是没有第四种持久化介质；缺点是人工检查需要数据库工具，而且无法形成独立、可分享给本地
调试工具的版本化 JSON。本方案仍把 PostgreSQL 作为事实来源，本地 JSON 只做诊断副本。

### 从 Milvus 导出

Milvus 只保存检索所需字段，且是派生索引。由它导出会把向量库实现细节反向变成审计格式，也无法
保证保存的是 Embedding 前的完整 metadata，因此不采用。

### 保存 Embedding 向量

向量体积大、与模型和维度绑定，并可由文本重建。用户需要的是 Chunk 与 metadata 检查能力，保存
向量会增加磁盘、安全和兼容成本，因此不采用。

### 快照失败仅记录日志并继续

Best-effort 会导致 Milvus 中已有数据、但本地没有对应审查副本，无法满足“入库前必须保存一份”的
明确要求，因此选择 fail-closed。

## Consequences

- 每个文档增加一份格式化 JSON 的本地磁盘占用和一次文件写入。
- 本地磁盘临时不可用会使任务重试或最终 FAILED，但不会产生额外 Embedding 费用或半成品向量。
- PostgreSQL 仍是 Chunk 事实来源；删除本地快照不影响在线检索，可通过 reindex 重建。
- 快照含业务明文，生产部署必须限制目录权限、监控容量并纳入数据删除流程。
- 原子替换保证读者不会看到半截 JSON；它不等同于数据库 WAL 的断电持久性，快照可从事实数据
  重建，因此没有为每个文件强制执行可能显著拖慢绑定挂载的 `fsync`。
