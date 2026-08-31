# ADR-004：用原 PDF + SourceLocator 按需重建视觉证据

## Context / 背景

V2 的 PDFParser 已能通过 Docling 识别图片、表格、分栏与阅读顺序，并把图片经百炼 OCR/Vision
转换为可检索语义，把表格转换为 Markdown。图片字节只在解析期间存在；完成模型提取后不会进入
Chunk、PostgreSQL 或 Milvus。因此回答可以使用图表语义，但前端证据卡只能显示文字。

PostgreSQL Chunk 已保存 PDF 一基页码与左上角原点 BBox，MinIO 保存完整原 PDF。这两类事实足以
重建用户核验所需的视觉区域。

## Decision / 决策

新增只读端点 `GET /api/chunks/{chunk_id}/preview`：

1. 用 Chunk ID 从 PostgreSQL 读取 Chunk 与 Document；
2. 只允许 `READY` PDF 且 Locator 含页码；
3. 用系统 Object Key 从 MinIO 读取原 PDF；
4. 使用本地 PDFium 按服务端固定倍率渲染页，并按可信 BBox + 固定留白裁剪；
5. 返回 JPEG、稳定 ETag 与私有缓存头。

检索最终结果批量回查 PostgreSQL，补齐命中 Child 的 `content_types`。API 返回类型和受控
`preview_url`；前端使用 GFM 渲染表格文本，并在展开证据时懒加载原文截图。

## Alternatives / 备选方案

### 解析时把每张图片/表格截图作为独立 MinIO 对象保存

拒绝作为当前方案。它能降低首次查看延迟，但新增对象命名、事务补偿、重解析覆盖、删除清理和
孤儿对象治理；同一表格被拆成多个 Child 时还要处理资产复用。当前页码/BBox 已可确定性重建，
额外事实副本成本不值得。

### 把 Base64 图片放进 PostgreSQL 或 Milvus

拒绝。二进制会放大事实表/向量索引、API 响应和备份体积；Milvus 仍应保持可重建派生索引职责。

### 前端下载完整 PDF 并自行裁剪

拒绝。浏览器需要获得完整原文，坐标转换和 PDF 渲染依赖进入前端，还会显著增加下载量与访问
控制面。服务端按 Chunk 渲染能只暴露回答所需区域。

## Consequences / 影响

- 旧文档只要已有页码/BBox 即可使用，无需重新 Embedding 或迁移 Milvus Schema；
- 查看预览不调用百炼 OCR/Vision，成本和结果稳定；
- MinIO + PostgreSQL 继续是事实来源，预览可丢弃重建；
- 首次展开会产生一次 PDFium CPU/内存开销，通过线程执行、固定参数、懒加载和缓存限制；
- 扫描页没有元素 BBox 时返回整页；非 PDF/非 READY/无页码返回 404；
- 当前权限模型尚未实现 V4 ACL，后续必须在同一 Chunk 读取边界加入知识库访问校验。
