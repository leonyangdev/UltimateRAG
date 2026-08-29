# Worker 后台任务

代码位置：`src/ultimate_rag/worker.py`

## 1. 这一层是什么

Worker 是**与 FastAPI 完全分离的独立进程**。上传请求只入队，Worker 进程循环领取任务并执行耗时处理（Parse → Chunk → Embed → Index）。这解决了「HTTP 请求内完成大文档处理」会阻塞、易超时、崩溃无恢复的问题。

## 2. 为什么不用 Redis/Kafka

V2 只用 PostgreSQL + `FOR UPDATE SKIP LOCKED` 就实现了可靠异步任务，**没有引入 Redis/Kafka**：

- 任务表本来就存在 PostgreSQL（Document + IngestionJob 同事务），没有额外基础设施
- `FOR UPDATE SKIP LOCKED` 天然支持多 Worker 并发领取不冲突
- 心跳续租处理 Worker 崩溃回收
- 保留了以后替换任务基础设施的清晰边界

## 3. 核心类：IngestionWorker

```python
IngestionWorker(
    repository,          # 任务事实端
    processor,           # DocumentProcessingService（真正干活）
    worker_id,           # hostname:pid:随机串
    poll_interval,       # 空队列轮询间隔
    lease_seconds,       # 租约时长
    heartbeat_seconds,   # 心跳间隔
    retry_delay_seconds, # 首次重试延迟
)
```

### 主循环 `run()`

```python
while not stop_event.is_set():
    processed = await self.run_once()
    if processed:
        continue
    # wait_for 同时提供低延迟停止和空队列退避
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
```

- 处理完一个任务立即领下一个
- 空队列用 `stop_event.wait()` 退避，**避免无任务时频繁查询 PostgreSQL**
- 信号（SIGINT/SIGTERM）触发 `stop_event` → 优雅停止

## 4. 单次领取与处理 `run_once()`

```python
job = await self._repository.claim_ingestion_job(
    self._worker_id, lease_seconds=self._lease_seconds)
if job is None:
    return False                      # 空队列

lease_lost = asyncio.Event()
heartbeat = asyncio.create_task(self._heartbeat(job, lease_lost))
try:
    await self._processor.process(job.document_id)
except asyncio.CancelledError:
    raise                              # 进程退出不篡改状态，租约到期后他人回收
except Exception as exc:
    await self._handle_processing_failure(job, exc, lease_lost.is_set())
else:
    if lease_lost.is_set():
        logger.error("completed after lease lost; another worker will reconcile")
    else:
        await self._repository.complete_ingestion_job(job.id, self._worker_id)
finally:
    heartbeat.cancel()
    await heartbeat
```

关键点：

- **单并发 Worker**（一次处理一个任务），V2 的简化策略
- 心跳是独立 Task，处理期间持续续租
- **租约丢失后不提交状态**：不能覆盖新 Worker 的进度，也不能删除它可能写入的向量

## 5. 心跳续租 `_heartbeat()`

```python
while True:
    await asyncio.sleep(self._heartbeat_seconds)
    renewed = await self._repository.heartbeat_ingestion_job(job.id, self._worker_id)
    if renewed:
        continue
    lease_lost.set()     # 租约被他人拿走
    return
```

- 每次心跳把租约续到「现在 + lease_seconds」
- 数据库不可达时无法证明仍拥有租约 → **按租约丢失处理**，禁止随后提交状态

## 6. 失败处理 `_handle_processing_failure()`

```python
if lease_lost:
    return   # 失去所有权，交给新 Worker

await self._processor.cleanup_partial_index(job.document_id)   # 清理半成品向量

# 只对"很可能是临时故障"的错误重试；参数/文件类错误不重试
retryable = not isinstance(exc, (InvalidDocumentError, ResourceNotFoundError))

# 指数退避：remaining attempts 内，封顶 5 分钟，避免请求风暴
retry_delay = min(self._retry_delay_seconds * (2 ** max(job.attempts - 1, 0)), 300)

will_retry = await self._repository.fail_ingestion_job(
    job.id, self._worker_id,
    error_message=message, retryable=retryable, retry_delay_seconds=retry_delay)
```

- 非临时错误（不支持格式、文档不存在）**不重试**，直接终态 FAILED
- 临时错误（网络、超时、外部服务 5xx）**有限指数退避重试**（最多 3 次尝试）
- 清理失败只记录，不影响重试决策（下次索引阶段会按 document_id 删除旧向量）

## 7. 启动入口

```python
def main():
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, ...)
    asyncio.run(run_worker(settings))
```

```text
uv run python -m ultimate_rag.worker
```

运行流程：创建 Processing Runtime → 注册优雅停止信号 → `run()` 循环 → 退出时关闭 Runtime。

## 8. 为什么这样能可靠

| 场景 | 结果 |
|---|---|
| Worker 正常完成 | `complete_ingestion_job` → 文档 READY |
| 处理中 Worker 崩溃 | 租约过期 → 其他 Worker `SKIP LOCKED` 领取 → 幂等重跑 |
| 临时外部故障 | 指数退避重试（≤3 次） |
| 永久错误 | 直接 FAILED，可排查 |
| 优雅停机 | 信号停止 → 不篡改状态 → 租约到期他人回收 |

## 下一步

- Worker 调的 `DocumentProcessingService` 到底干了什么 → [Application 应用层](/modules/application)
- 完整入库链路 → [文档摄取全流程](/workflows/ingestion)
- 状态机推进规则 → [文档状态机](/workflows/state-machine)
