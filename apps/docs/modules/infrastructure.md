# Infrastructure 基础设施层

代码位置：`src/ultimate_rag/infrastructure/`

## 1. 这一层是什么

Infrastructure 层是**外部依赖的具体实现**：PostgreSQL、MinIO、Milvus、模型 API 都在这里被适配成领域端口。它被 Domain 反向依赖（Domain 定义端口，Infrastructure 实现端口）。

```text
Interface
    ↓
Application  →  依赖领域端口（Protocol）
    ↓
Domain  ←────  实现端口
    ↑
Infrastructure（PostgreSQL / MinIO / Milvus / 模型 API）
```

## 2. 数据库层

### 2.1 表结构（`database/models.py`）

四个 SQLAlchemy 模型对应四张 PostgreSQL 表：

| 表 | 保存内容 | 关键约束 |
|---|---|---|
| `knowledge_bases` | 知识库事实 | 级联删除文档 |
| `documents` | 文档元数据 + 处理状态 | `object_key` 唯一；级联 Chunk/任务 |
| `ingestion_jobs` | 持久化任务 + 租约 | `document_id` 唯一；索引 `(status, available_at)` |
| `chunks` | Chunk 事实（可重建 Milvus） | `heading_path` / `chunk_metadata` 用 JSONB |

关键设计：

- **`object_key` 唯一**：系统生成的键，杜绝重复对象
- **级联删除**：删知识库 → 文档 → Chunk/任务，全部在一个事务里
- **IngestionJob 的 `document_id` 唯一**：一个文档最多一条任务
- `(status, available_at)` 组合索引：支撑 Worker 的领取查询

### 2.2 Repository（`database/repository.py`）

Repository 是**面向业务语义**的数据访问层，不拼裸 SQL：

```python
# 业务方法示例
create_knowledge_base / list_knowledge_bases / get_knowledge_base
create_document_with_job       # 文档+任务同事务
claim_ingestion_job            # FOR UPDATE SKIP LOCKED 领任务
heartbeat_ingestion_job        # 续租
complete_ingestion_job / fail_ingestion_job
replace_chunks                 # 先删后插，事务内保证重试不重复
update_document_status         # 状态 + error_message 同事务
list_ready_document_ids        # 检索过滤用
```

要点：

- **ORM 模型不越过边界**：Repository 方法统一返回不可变领域对象（`Document`、`IngestionJob`），上层永远不接触 SQLAlchemy 实例
- **每个公共操作自行定义短事务**（`async with session.begin()`）
- `claim_ingestion_job` 用 `FOR UPDATE SKIP LOCKED` 支持多 Worker 并行领取，并**自动回收租约过期任务**

## 3. 对象存储层（`storage/minio.py`）

`MinioObjectStorage` 实现 `ObjectStorage` 端口：

```python
ensure_bucket()   # 幂等创建 Bucket
put(object_key, content, content_type)   # 上传原始文件
get(object_key) -> bytes                 # 读取并关闭 HTTP 连接
delete(object_key)                       # 删除（键由应用生成）
```

- MinIO SDK 是同步接口 → 全部 `asyncio.to_thread`，不阻塞事件循环
- `get()` 用 `finally` 保证 `response.close()` + `release_conn()`，防止连接池泄漏
- **目标键只能由应用生成**，不能直接来自用户文件名（路径穿越防护）

## 4. 依赖装配（`runtime.py` —— Composition Root）

`create_processing_runtime(settings)` 是 **API 与 Worker 共用的装配入口**：

```python
def create_processing_runtime(settings) -> ProcessingRuntime:
    engine, repository = create_database(settings.database_url)
    storage = MinioObjectStorage(...)
    embedder = BailianEmbedder(...)
    vector_store = MilvusVectorStore(...)
    ocr = BailianOCRClient(...)
    vision = BailianVisionClient(...)

    # Parser Registry：顺序即优先级（Markdown → Word → Excel → PPT → HTML → PDF → 图片）
    registry = ParserRegistry([MarkdownParser(), WordParser(), ..., ImageOCRParser(ocr)])

    chunker = StructureAwareChunker(...)
    ingestion = IngestionService(...)
    processor = DocumentProcessingService(...)
    return ProcessingRuntime(...)
```

设计价值：

- **一个 Composition Root**：API 上传校验与 Worker 真正解析使用**同一个 Parser Registry**，避免两套进程支持格式不一致
- **`ProcessingRuntime.initialize()`** 幂等准备 MinIO Bucket + Milvus Collection
- **`close()`** 释放 SQLAlchemy 连接池
- 不引入 DI 框架：当前依赖数量有限，显式装配即可

## 5. 四个表里谁是真来源

| 存储 | 是否真来源 | 说明 |
|---|---|---|
| PostgreSQL | ✅ 事实来源 | 文档、任务、Chunk 事实 |
| MinIO | ✅ 原始文件 | 可排查、可重建一切派生数据 |
| Milvus | ❌ 派生索引 | 可由 PostgreSQL Chunk + Embedder 重建 |

## 下一步

- 数据库表 / 领域模型的对应关系 → [数据模型](/architecture/data-model)
- HTTP 入口怎么用这些组件 → [API 与前端](/modules/api-web)
