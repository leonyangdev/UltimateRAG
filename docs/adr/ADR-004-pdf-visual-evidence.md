# ADR-004：持久化图片 Asset，并按需重建其他 PDF 视觉证据

## Context / 背景

V2 PDFParser 已能通过 Docling 识别图片、表格、分栏与阅读顺序，并使用百炼 Vision 把图片转换为
可检索语义。但旧实现完成模型提取后就释放裁图字节，Chunk 只剩描述和 BBox。结果是 Retrieval
可以命中“Transformer 架构图”，模型和前端却没有一份可引用的图片资源，最终回答“无法展示图片”。

`GET /api/chunks/{chunk_id}/preview` 虽然可以从原 PDF + Locator 动态裁切证据，却只挂在折叠证据
卡，模型 Context 不知道该资源，也无法在答案正文发出稳定引用。历史消息又只保存正文，刷新后
连当轮 Retrieval Evidence 都会丢失。

## Decision / 决策

### 1. PDF Parser 同时产生语义 Block 与 ParsedAsset

Docling 图片经 Vision/OCR 后，在原阅读位置形成：

```markdown
![Transformer 架构图](asset://<stable-id>)

图片解读：Encoder 与 Decoder……
```

同一元素还产生含 JPEG 字节、Block ID、题名、描述和 Locator 的 `ParsedAsset`。Asset ID 由文档、
页码、阅读顺序与 BBox 使用 UUID5 生成，重试保持稳定；Parser 不依赖 MinIO 或 SQLAlchemy。

### 2. MinIO 保存图片，PostgreSQL 保存资源事实

Worker 使用 `{kb}/{document}/assets/{asset-id}.jpg` 保存二进制，并在 `document_assets` 记录
Block、Object Key、MIME、SHA-256、题名、描述和 Locator。Chunk metadata 只携带 `asset_ids`；
Milvus 不保存二进制、内部 Key 或资源事实。

资源完成后才继续 Embed/Index/READY。重新解析先稳定覆盖新对象、清理不再存在的旧对象，再以
单库事务替换元数据。文档/知识库删除按 Vector → Asset/Source Object → PostgreSQL 的顺序执行。

### 3. 检索与生成使用受控内部协议

最终 RetrievalResult 从 PostgreSQL 批量补齐 `content_types + assets`。ContextBuilder 只向模型
声明真实资源的精确 `asset://ID`，系统 Prompt 要求在用户请求图片时复制完整 Markdown，禁止
编造 ID 或外部 URL。引用写成 `[来源 N](citation://N)`。

前端只有在本消息 RetrievalResult 的 Asset 白名单中找到 ID 才映射
`GET /api/assets/{id}/content`；任意公网图片不会自动加载。Citation 链接只打开本地右侧来源栏，
详情来自后端结果，不从模型自由文本反向推断。

### 4. 保留 PDFium 动态预览作为互补路径

表格源数据仍为 GFM Markdown。表格、正文和未抽取区域继续使用 `GET /api/chunks/{id}/preview`，
按 PostgreSQL 页码/BBox 从 MinIO 原 PDF 本地裁切。查看阶段不调用 OCR/Vision。

### 5. 历史消息保存证据快照

助手正文与 `ChatEvidence(Citation, RetrievalResult, Trace)` 在同一事务提交到 `chat_messages` 的
JSONB。刷新或选择历史会话后可以恢复相同图片和来源，不重新检索。Migration
`0004_document_assets` 同时增加资源表与该快照列。

## Alternatives / 备选方案

### 只使用动态 PDF BBox 截图

保留为表格/正文的互补方案，但不足以支持答案正文图片：模型不知道截图 URL，历史资源关联也不
稳定；每次展示还需要重新打开并渲染完整 PDF 页面。

### 给 MinIO Bucket 配公开 URL或把预签名 URL 写入 Chunk

拒绝。公开 Bucket 破坏访问边界；预签名 URL 会过期，不能持久化到 Chunk、Prompt 或历史会话。
系统只保存稳定 Asset ID，请求时由受控 API 读取。

### 把 Base64 图片放进 PostgreSQL、Prompt 或 Milvus

拒绝。二进制会放大数据库、向量索引、模型上下文、API 与备份体积，也模糊三类存储职责。

### 允许模型直接输出任意 Markdown 图片 URL

拒绝。恶意文档可诱导浏览器请求第三方跟踪像素，泄露 IP、Referer 或查询语义。前端必须执行
本消息 Asset 白名单校验。

## Consequences / 影响

- 新解析 PDF 可以在回答正文直接显示论文图片，并继续用文字语义参与 Dense/BM25/Rerank；
- 表格保留可复制的 Markdown 源数据，来源链接可打开右侧侧栏核验原文；
- 存量 READY 文档需要调用 reindex 重新解析，工作台提供复用原文件的后台入口；
- MinIO 对象数量与摄取期写入增加，但每张图片有稳定 Key、SHA-256、删除清理和数据库事实；
- 查看持久化图片不再消耗 PDFium CPU 或百炼费用，动态 BBox 预览仍受固定倍率与缓存限制；
- 当前权限模型尚未实现 V4 ACL，未来必须在 Asset/Chunk 两个读取边界统一加入知识库授权。
