# ADR-001：V2 提前引入可靠异步摄取与本地 PDF 版面分析

## Status

Accepted — 2026-08-29

## Context / 背景

原路线图把完整异步 Pipeline 放在 V4，V2 初版因此在上传 HTTP 请求内同步执行：

```text
Upload → Parse → Chunk → Embed → Index → READY → HTTP Response
```

复杂 PDF 会触发版面推理、扫描页 OCR、图片理解和多批 Embedding。继续同步执行会带来长连接、
网关超时、浏览器重复提交以及 API 进程 CPU/内存被解析任务占用等问题。用户已明确要求 V2 上传
成功后立即返回，并能在前端观察后台解析状态，因此当前需求覆盖原路线图的同步限制。

同时，PDFium 的整页文字提取只能提供粗粒度文本，无法可靠处理双栏阅读顺序、标题层级、表格
结构、图片区域和元素坐标；简单字符窗口也无法同时适配正文、代码和表格。

## Decision / 决策

### 1. 使用 PostgreSQL 持久化摄取任务

新增 `ingestion_jobs` 表，由独立 Worker 使用：

```text
POST Upload
  → Validate
  → MinIO Put
  → PostgreSQL Transaction(Document + IngestionJob)
  → 202 Accepted / PENDING

Worker
  → SELECT ... FOR UPDATE SKIP LOCKED
  → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
```

- 文档与任务在同一事务创建，消除“有 PENDING 文档但没有任务”的窗口。
- `SKIP LOCKED` 允许多个 Worker 互不阻塞地领取不同任务。
- `locked_at`、`worker_id` 和定时心跳形成任务租约，进程崩溃后可回收。
- 只对临时故障有限重试；损坏文件、非法格式不重试。
- 重试仍使用稳定 Block/Chunk ID 和文档级向量删除，避免重复数据。
- Milvus 命中会由 PostgreSQL `READY` 事实二次过滤，半成品索引不能参与回答。

当前不引入 Redis、Celery、Kafka 或分布式调度器。任务吞吐、优先级或跨服务编排达到明确瓶颈后，
再通过任务 Repository 边界演进；V4 的 DLQ、运维控制台、审计与大规模调度范围保持不变。

### 2. 文字型 PDF 使用本地 Docling，扫描页使用百炼

PDF 采用双路径：

```text
PDFium 安全打开与逐页文字探测
  ├─ 低文字量页 → 本地渲染 JPEG → 百炼 Qwen OCR
  └─ 文字型页   → 本地 Docling Layout + TableFormer
                        ├─ 标题/正文/列表/代码
                        ├─ 分栏阅读顺序
                        ├─ 表格结构 → Markdown
                        ├─ Page + BBox
                        └─ Picture Crop → 百炼 Qwen-VL 视觉理解
```

- Docling 关闭自身 OCR，避免同时维护两个 OCR 结果来源。
- Docling 模型在 Worker 首次文字型 PDF 任务中延迟加载，API 上传进程不加载 Torch。
- 模型完全在本地运行；只有扫描页和裁剪后的文档图片发送到 `.env` 配置的阿里云百炼。
- 图片理解失败会降级为 OCR；单张附图失败不使已有正文失效，完整扫描页 OCR 失败则由任务重试。
- 重复页眉页脚不进入索引；页码和左上角原点 BBox 进入 `SourceLocator`。
- Docker Volume 持久化模型缓存，避免容器重建后重复下载。
- 默认 uv 锁文件把 `torch/torchvision` 固定到 PyTorch 官方 CPU Index，避免 CPU 容器安装 CUDA 依赖；
  GPU Worker 应使用独立镜像与锁定策略。

### 3. 使用结构 + Token + 类型感知的混合切块

默认从 `512 tokens / 64 overlap` 起步，但把它视为需要 Retrieval Evaluation 调优的基线，而不是
脱离文档和查询分布的“数学最优值”。切分顺序为：

1. 严格保留标题、页码、Sheet/Range、Slide 等来源边界；同页元素 BBox 可合并。
2. 表格按行切分，每个 Chunk 重复表头。
3. 代码按行切分，每个 Chunk 保持独立围栏。
4. 正文优先段落，再按句子，最后才用 Token 窗口。
5. 自然单元间只携带不超过预算的尾部 overlap；新信息优先于强行重叠。
6. 标题上下文写入每个 Chunk，但限制其预算，防止超长标题挤掉正文。

本地使用 `cl100k_base` 做一致的中英文 Token 预算近似。百炼 Embedding 没有在当前本地 SDK 暴露
同模型 Tokenizer，因此文档明确记录这不是供应商精确计数；上线前应使用项目评估集比较不同大小。

## Alternatives / 备选方案

### FastAPI `BackgroundTasks`

拒绝。任务只存在于 API 进程内，进程重启会丢失；CPU 密集 PDF 仍与请求服务争用资源，也没有
跨副本的安全领取与租约恢复。

### Redis/Celery 或 Kafka

暂不采用。它们能提供更高吞吐和成熟调度，但当前只有单一确定性文档任务，引入额外服务、协议、
监控和故障面不符合“解决当前真实问题的最简单架构”。

### 只使用 PDFium/pdfplumber

拒绝作为主路径。它们适合底层文字、对象和表格规则提取，但复杂分栏、阅读顺序、跨单元格表格与
图片语义仍需要大量启发式代码。PDFium 继续承担安全打开、扫描判定和页面渲染这一明确职责。

### 每页全部发送在线视觉模型

拒绝。会增加数据外发、模型费用、延迟和限流风险，也浪费本地可确定恢复的原生文字与表格结构。

### Embedding 驱动的语义切分

暂不作为默认。它会在正式 Embedding 前增加额外模型调用和阈值，成本更高且未必改善表格/代码；
结构 + Token 是可解释、可复现的强基线，后续由评估数据决定是否加入语义断点。

## Consequences / 影响

正向影响：

- 上传延迟与文档复杂度解耦，浏览器能可靠看到处理进度。
- Worker 可以横向扩展，崩溃任务可自动恢复。
- 复杂 PDF 的阅读顺序、表格、图片和 BBox 可追踪。
- Chunk 预算真实按 Token 执行，表格和代码不再被普通字符窗口破坏。

成本与限制：

- API/Worker 镜像因 Docling/Torch 明显增大，首次文字型 PDF 需要下载本地模型。
- 当前任务表是轻量数据库队列，不包含优先级、DLQ UI、任务取消和大规模调度能力。
- 本地版面分析需要 CPU/内存容量；生产环境应为 Worker 单独设置资源限制与副本数。
- `cl100k_base` 是预算近似，最终参数必须通过真实问答评估而不是只看平均 Chunk 长度。
- Office 内嵌图表、宏和旧格式仍不在本次范围。

## Primary references / 一手资料

- [PostgreSQL `SELECT` / `SKIP LOCKED`](https://www.postgresql.org/docs/current/sql-select.html)
- [Docling 官方文档](https://docling-project.github.io/docling/)
- [Docling PDF pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/)
- [Docling Hybrid Chunking](https://docling-project.github.io/docling/concepts/chunking/)
- [pdfplumber 官方 README](https://github.com/jsvine/pdfplumber/blob/stable/README.md)
- [阿里云百炼视觉模型](https://help.aliyun.com/en/model-studio/vision-model/)
- [阿里云百炼 Qwen OCR](https://help.aliyun.com/en/model-studio/qwen-vl-ocr)
- [Microsoft Advanced RAG：chunking 与组织](https://learn.microsoft.com/en-us/azure/developer/ai/advanced-retrieval-augmented-generation)
- [Milvus RAG chunking 指南](https://milvus.io/ai-quick-reference/how-do-i-implement-efficient-document-chunking-for-rag-applications)
