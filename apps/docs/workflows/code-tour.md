# 核心代码导读

这是一份**代码阅读地图**：从哪里开始读、每个文件看什么、文件之间怎么衔接。目标是让你读一遍源码就能把整个项目串起来。

## 1. 推荐阅读顺序

```text
第一遍（30 分钟，建立全貌）：
  领域层 → 装配 → 服务 → 一个 Parser → Worker

第二遍（按链路精读）：
  摄入链路：submit → process → repository → 各 adapter
  问答链路：route → rag → retrieval → context → llm

第三遍（深入细节）：
  Parser 全家 / Chunker 算法 / Milvus 一致性 / 状态机边界
```

## 2. 第一站：领域层 —— 项目的「词汇表」

先读 `src/ultimate_rag/domain/models.py`，这是所有模块共享的领域对象：

| 看什么 | 为什么重要 |
|---|---|
| `DocumentStatus` 枚举 | 文档状态机（PENDING…READY…FAILED） |
| `IngestionJobStatus` 枚举 | 任务状态机 |
| `SourceLocator` | 跨格式来源定位（page/bbox/sheet/slide） |
| `Block` / `ParsedDocument` | Parser 的统一输出 |
| `Chunk` / `EmbeddedChunk` | 切块与向量化的连接 |
| `RetrievalResult` / `Citation` | 检索与问答的输出 |

接着看 `domain/ports.py`（8 个 Protocol 端口）和 `domain/exceptions.py`（业务异常体系）。**先理解端口，再看实现**，因为所有模块都围绕端口协作。

## 3. 第二站：配置与装配

`src/ultimate_rag/config.py` —— 所有环境变量默认值（看一眼有哪些配置组即可）。

`src/ultimate_rag/runtime.py` —— **Composition Root**。这里能看到完整依赖图：

```python
create_processing_runtime(settings):
    create_database(...)      # 数据库 Engine + Repository
    MinioObjectStorage(...)   # 对象存储
    BailianEmbedder(...)      # 向量化
    MilvusVectorStore(...)    # 向量库
    BailianOCRClient(...)     # OCR
    BailianVisionClient(...)  # 视觉理解
    ParserRegistry([...])     # 7 个 Parser，顺序即优先级
    StructureAwareChunker(...)
    IngestionService(...)     # 上传入队
    DocumentProcessingService(...)  # 后台处理管线
```

> 💡 从 `runtime.py` 开始读，是理解「谁依赖谁」最快的入口。

## 4. 摄入链路代码导读

### 4.1 入口：`apps/api/routes.py` → `upload_document`

```python
content = await _read_bounded_upload(file, dependencies.max_upload_bytes)  # 有界读取
value = await dependencies.ingestion.submit(...)   # 入队，不等待
return DocumentResponse.from_domain(value)          # 202 + PENDING
```

### 4.2 入队：`application/services.py` → `IngestionService.submit`

关注顺序：校验 → 系统对象键 → 预校验 Parser → **先存 MinIO** → **Document+Job 同事务** → 失败补偿删除。

### 4.3 领取：`infrastructure/database/repository.py` → `claim_ingestion_job`

这段 SQL 是任务系统的核心，重点看：

```python
# 先收敛租约过期且尝试耗尽的任务
# 再领取：attempts < max AND (PENDING 且到时间 OR RUNNING 且租约过期)
.with_for_update(skip_locked=True)   # 多 Worker 不冲突
```

### 4.4 处理：`application/services.py` → `DocumentProcessingService.process`

四个阶段（Parse → Chunk → Embed → Index），每阶段先更新状态再干活。**READY 在最后。**

### 4.5 数据落库：`repository.py` → `replace_chunks` / `update_document_status`

- `replace_chunks`：事务内「先删后插」，重试不产生重复
- `update_document_status`：状态 + error_message 同事务，推进时清空旧错误

### 4.6 Worker：`src/ultimate_rag/worker.py` → `IngestionWorker`

- `run_once()`：领取 → 启动心跳 Task → 处理 → 提交/失败
- `_heartbeat()`：续租，租约丢失立即置 `lease_lost`
- `_handle_processing_failure()`：清理半成品 + 有限重试决策

## 5. 解析器代码导读

`src/ultimate_rag/parsers/`：

| 文件 | 看点 |
|---|---|
| `registry.py` | 如何按 `supports()` 顺序选 Parser |
| `_shared.py` | 输入安全（ZIP Bomb 防护、扩展名/MIME 校验）、表格转 Markdown |
| `markdown.py` | 标题路径如何维护：`heading_path = heading_path[:level-1]; append(content)` |
| `pdf.py` | 最复杂：PDFium 逐页探测 + Docling 布局 + 扫描页 OCR 双路径 |
| `office.py` | Word/Excel/PPT 各自的结构映射 |
| `image.py` | Pillow 验证真实格式 → 百炼 OCR |

> 读懂一个 Parser（推荐 Markdown），就懂了全部：都是「原始格式 → Block[]」的映射。

## 6. 切块器代码导读

`src/ultimate_rag/chunkers/markdown.py` → `StructureAwareChunker`：

| 方法 | 看什么 |
|---|---|
| `_build_sections` | 怎么把 Block 聚合为「同来源 + 同策略」的 Section |
| `_split_section` | 按内容类型选择切分策略（正文/表格/代码/图片） |
| `_split_natural` | 自然单元贪心装箱 + 尾部 overlap 处理 |
| `uuid5` 稳定 ID | 幂等重试的基础 |
| Token 硬预算 | 超限显式报错，不静默 |

## 7. 向量化与向量库

`src/ultimate_rag/embeddings/bailian.py`：

```python
# 按 batch 分批 → index 排序恢复顺序 → 数量/维度校验
ordered = sorted(response.data, key=lambda item: item.index)
self._validate(embeddings, len(texts))   # 阻止错误向量进入 Milvus
```

`src/ultimate_rag/vectorstores/milvus.py`：

- `ensure_collection`：幂等，已存在则复用
- `upsert`：稳定 Chunk ID 为主键 + **Flush 落盘后才返回**
- `search`：`knowledge_base_id` 过滤在 Milvus 层完成
- 同步 SDK 全部 `asyncio.to_thread`

## 8. 问答链路代码导读

| 文件 | 看什么 |
|---|---|
| `routes.py` → `stream_chat` | 检索先于响应开始；SSE 事件顺序（start→data-retrieval→text-delta→finish） |
| `services.py` → `RAGService._prepare_generation` | 无证据降级、XML 标签隔离、Citation 构造 |
| `services.py` → `RetrievalService.search` | 二次过滤（Milvus 候选 → PostgreSQL READY 过滤） |
| `context.py` → `ContextBuilder` | 字符预算内拼接 `[来源 N]`，顺序不重排 |
| `generation/bailian.py` | 完整生成 vs 原生增量流；空答案拒绝 |
| `app.py` → 异常映射 | 404/400/409/502 的业务异常对应 |

## 9. 状态一致性代码清单

这些位置共同保证「未 READY 不可检索」：

```text
repository.py  →  create_document_with_job   （文档+任务同事务）
repository.py  →  update_document_status      （状态+错误同事务）
repository.py  →  replace_chunks              （先删后插，重试不重复）
services.py    →  process                     （READY 放最后）
services.py    →  search                      （READY 二次过滤）
worker.py      →  _handle_processing_failure  （清理半成品向量）
```

## 10. 如果你想测试自己的理解

跟着下面这些问题读代码：

1. 上传后为什么能立刻返回 202？Document 和 IngestionJob 怎么保证同时存在？
2. 两个 Worker 同时领任务会发生什么？`SKIP LOCKED` 怎么避免冲突？
3. Worker 处理到一半崩溃，任务和文档分别停在什么状态？谁来自动恢复？
4. Chunk ID 为什么稳定？它怎么让重试幂等？
5. 检索为什么多取 3 倍候选再过滤？READY 过滤解决什么问题？
6. 流式问答中，为什么检索和 Citation 在响应建立前完成？
7. 如果删除文档时 PostgreSQL 删成功了但 MinIO 失败，会怎样？（答：见 `delete_document` 的顺序）
8. Milvus 丢失了怎么重建？（答：PostgreSQL Chunk 事实 + Embedder 重新向量化）

## 下一步

- 回到模块详解 → [模块详解总览](/modules/)
- 全部 API 端点与响应结构 → [API 参考](/reference/api)
