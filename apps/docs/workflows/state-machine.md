# 文档状态机

文档处理有**两套互相配合的状态**，理解它们的对应关系是理解整个系统的钥匙：

```text
DocumentStatus（文档处理状态，用户可见）    IngestionJobStatus（任务状态，Worker 驱动）
```

## 1. 文档状态：DocumentStatus

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
                                  │
                                  └─ 任何阶段失败 → FAILED
```

| 状态 | 含义 | 可被检索？ |
|---|---|---|
| `PENDING` | 已入队，等待处理 | ❌ |
| `PARSING` | 正在解析原文件 | ❌ |
| `CHUNKING` | 正在切块 | ❌ |
| `EMBEDDING` | 正在向量化 | ❌ |
| `INDEXING` | 正在写 Chunk 与向量 | ❌ |
| `READY` | **全部步骤成功**，可检索 | ✅ |
| `FAILED` | 处理失败（含可读错误信息） | ❌ |

### 核心规则

1. **READY 是提交标志**：只有 Parse → Chunk → Embed → Index 全部成功后才设置
2. **状态先于动作**：每个阶段开始前先更新状态。进程中断时，数据库停留在「最后开始的阶段」，便于定位
3. **绝不让未完成文档可用**：未 READY 的文档不参与检索（检索层用 `READY` 过滤）
4. **失败保留可理解状态**：`FAILED` + `error_message` 供用户排查

## 2. 任务状态：IngestionJobStatus

```text
PENDING → RUNNING → SUCCEEDED
   │         │
   └──失败───┘
        ↓
   FAILED（终态）   或   回 PENDING（有限重试，延迟后重新可见）
```

| 状态 | 含义 |
|---|---|
| `PENDING` | 可被 Worker 领取（含重试等待中的延迟） |
| `RUNNING` | 已被某 Worker 领取，持有租约 |
| `SUCCEEDED` | 处理成功（文档已 READY） |
| `FAILED` | 重试耗尽 / 永久错误 |

### 领取与租约

```python
# claim_ingestion_job 的领取条件
attempts < max_attempts
  AND (
    status = PENDING  AND available_at <= now()
    OR  status = RUNNING AND locked_at <= now() - lease_seconds   # 回收过期租约
  )
```

- `FOR UPDATE SKIP LOCKED`：多 Worker 并行领取不冲突
- `locked_at` 租约：Worker 崩溃后任务自动重新可见，**无需人工修改数据库**
- `available_at`：重试延迟，让失败任务退避后再出现

## 3. 两套状态的对应关系

| 时间线 | DocumentStatus | IngestionJobStatus |
|---|---|---|
| 上传提交 | `PENDING` | `PENDING`（同事务创建） |
| Worker 领取 | `PENDING` | `RUNNING`（attempts+1，locked_at 续期） |
| 处理中 | `PARSING` → `CHUNKING` → `EMBEDDING` → `INDEXING` | `RUNNING`（心跳续租） |
| 全部成功 | `READY` | `SUCCEEDED` |
| 临时失败（可重试） | 回 `PENDING` + 错误信息 | 回 `PENDING`，`available_at` 延迟 |
| 永久失败 / 重试耗尽 | `FAILED` | `FAILED` |

**两套状态在同一 PostgreSQL 事务中更新**，前端不会观察到矛盾状态。

## 4. 失败与重试的转移规则

### 临时失败（可重试）

```python
should_retry = retryable and model.attempts < model.max_attempts
if should_retry:
    job.status = PENDING
    job.available_at = now + timedelta(seconds=retry_delay)   # 指数退避
    document.status = PENDING
    document.error_message = "第 N/max 次处理暂时失败，系统将自动重试：..."
```

- 触发条件：非 `InvalidDocumentError` / `ResourceNotFoundError` 的异常
- 退避：`retry_delay_seconds × 2^(attempts-1)`，封顶 5 分钟
- **文档回 PENDING**：此时未 READY，天然不可检索，无并发风险

### 永久失败 / 重试耗尽

```python
else:
    job.status = FAILED
    document.status = FAILED
    document.error_message = safe_error
```

### 成功推进时清理错误信息

```python
model.status = status.value
model.error_message = error_message   # 推进新阶段时默认清空
```

避免一次失败重试成功后，仍向用户展示已经过期的错误原因。

## 5. 边缘情况：租约过期的收敛

`claim_ingestion_job` 在正式领取前，先对**租约过期且尝试耗尽**的任务做终态收敛：

```python
if attempts >= max_attempts and locked_at <= stale_before:
    is_ready = document.status == READY
    exhausted.status = SUCCEEDED if is_ready else FAILED
    if not is_ready:
        document.status = FAILED
        document.error_message = "Worker 租约过期且已达到最大尝试次数"
    return None   # 本次不领取，等下一个任务
```

- 若文档已 READY → 任务 SUCCEEDED（只是「完成提交」窗口崩溃）
- 否则 → 文档 FAILED，**不能永久停在 RUNNING**

## 6. 谁在推进状态

```text
DocumentProcessingService.process
    update_document_status(PARSING)   → parser.parse()
    update_document_status(CHUNKING)  → chunker.split()
    update_document_status(EMBEDDING) → embedder.embed_documents()
    update_document_status(INDEXING)  → replace_chunks + delete + upsert
    update_document_status(READY)     ← 全部成功，最后一步
```

`IngestionWorker` 驱动 process，并负责任务层面的领取、续租、提交、失败处理。

## 7. 状态机的意义

| 设计 | 解决的问题 |
|---|---|
| 状态先于动作 | 崩溃后能定位「进行到哪个阶段」 |
| READY 放最后 | 绝不把半成品文档暴露给检索 |
| 同事务更新 | 文档与任务状态永远一致 |
| 租约回收 | Worker 崩溃自动恢复，无人值守 |
| 有限重试 | 临时故障自愈，永久错误尽早暴露 |
| READY 过滤 | 即使有半成品向量，检索层也会拦截 |

## 下一步

- 状态由谁推进、每步干了什么 → [文档摄取全流程](/workflows/ingestion)
- 具体代码怎么实现这些规则 → [核心代码导读](/workflows/code-tour)
