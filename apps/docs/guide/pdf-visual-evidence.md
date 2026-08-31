# PDF 图片与表格如何进入对话证据

这页解释一个容易混淆的问题：**“模型理解了 PDF 图片/表格”**与**“用户能在对话框看到原图/原表”**是两条不同的数据链路。前者服务检索和生成，后者服务事实核验。

## 1. 修复前为什么只能看到文字

摄取阶段已经完成了以下工作：

- Docling Layout 恢复分栏阅读顺序、标题、表格和图片区域；
- TableFormer 把表格导出为 Markdown；
- PDF 图片裁图交给百炼 OCR/Vision，转成可检索的语义描述；
- 页码与 BBox 写入 `SourceLocator`，并随 Chunk 保存到 PostgreSQL。

但图片字节只是解析期间的临时数据，Vision 完成后就释放；Milvus 也只保存检索必需的文本与定位。因此旧版证据卡只能显示 Chunk 文字，且把 Markdown 表格放在普通 `<p>` 中，最终出现“能回答、看不到图表”的断层。

## 2. 现在的双轨证据模型

```text
摄取期（一次）
MinIO 原 PDF
  → PDFium 安全探测
  → Docling Layout / TableFormer（本地）
  → 图片 Crop → 百炼 OCR/Vision（线上）
  → Block(TABLE / IMAGE) + Markdown/语义文本 + page/BBox
  → Chunk metadata（PostgreSQL）+ 文本向量（Milvus）

问答期（每次检索）
Milvus 召回 Chunk
  → PostgreSQL 补齐 content_types 与可信 SourceLocator
  → data-retrieval 随 assistant message 返回
  → 对话框渲染 Markdown 表格 + PDF 局部预览

查看预览（按需、浏览器 lazy-load）
GET /api/chunks/{chunk_id}/preview
  → PostgreSQL Chunk → Document
  → MinIO 原 PDF
  → PDFium 按 page/BBox 本地裁图
  → image/jpeg
```

两条轨道各司其职：

| 轨道 | 目标 | 数据形态 |
|---|---|---|
| 语义轨道 | 让 Retriever/LLM 能理解内容 | Markdown 表格、OCR 文本、Vision 图片描述 |
| 视觉轨道 | 让用户核验原文 | 原 PDF 页面的可信 BBox 局部截图 |

## 3. 为什么不把每张图片再存一份 MinIO 对象

本版本选择**按需从原 PDF 重建预览**：

- 原 PDF 已经是 MinIO 中的事实数据，不新增图片对象生命周期与清理一致性；
- PostgreSQL 的页码/BBox 足以确定性重建截图；
- 旧文档只要已有 Locator，无需重新解析、重新 Embedding 或迁移 Milvus；
- 表格不仅有 Markdown 结构，还能同时看到论文排版中的合并表头、脚注与强调样式。

代价是首次展开证据时需要一次 PDFium 栅格化。响应使用私有 24 小时缓存与稳定 ETag，重复查看通常返回浏览器缓存或 `304`。

## 4. 安全和资源边界

预览接口只接收 `chunk_id`，**不接收 page、bbox、scale、quality 等客户端参数**。服务端按以下顺序校验：

1. Chunk 必须存在；
2. 所属 Document 必须为 `READY`；
3. 原文扩展名必须是 `.pdf`；
4. Chunk 必须有一基页码；
5. BBox 被夹紧在真实页面边界内，无效区域明确失败；
6. 渲染倍率、留白和 JPEG 质量由服务端固定。

PDFium 的 CPU 栅格化在线程中执行，不阻塞 FastAPI Event Loop；预览不执行 OCR、不调用 Vision，也不会把原始 PDF 整体发送给浏览器。

## 5. 对话框如何呈现

`RetrievalResult` 新增：

```json
{
  "content_types": ["TABLE"],
  "preview_url": "/api/chunks/chunk-abc/preview"
}
```

前端证据卡据此：

- 使用 `react-markdown + remark-gfm` 渲染表格，不再把 Markdown 当普通文本；
- 对 `IMAGE` / `TABLE` 显示类型徽标；
- 用原生懒加载图片显示 PDF 局部证据，点击可在新窗口查看大图；
- 图片加载失败时只隐藏预览，保留已召回文字，避免辅助展示故障破坏答案可用性；
- Small2Big 展开邻居后仍以真正命中的 Child 类型为准，防止正文命中被邻居图片误标。

## 6. 数据库存什么，Milvus 存什么

PostgreSQL `chunks.chunk_metadata` 保存可重建事实：

```json
{
  "block_types": ["TABLE"],
  "source_locator": {
    "page": 8,
    "bbox": [132.1, 36.4, 476.7, 214.9],
    "heading_path": ["6 Results"]
  },
  "layout_engines": ["docling"],
  "split_strategy": "table_rows"
}
```

Milvus 仍是可重建的派生索引，只保存 Dense/BM25 召回需要的内容和 Locator。最终结果在应用层批量回查 PostgreSQL 补齐 `content_types`，因此这次升级不需要修改 Milvus Schema。

## 7. 已知边界

- 加密、损坏或页码越界的 PDF 不生成预览；
- 扫描页只有页码没有元素 BBox 时展示整页；
- 当前展示的是命中 Chunk 的视觉锚点，不是从答案句子反向定位任意像素；
- 历史会话仍只持久化消息正文，本轮 Retrieval Evidence 刷新后不会恢复；这与视觉预览本身无关。

## 下一步

- PDF 如何识别分栏、表格与图片 → [Parser 解析器](/modules/parsers)
- 证据如何随 SSE 到达前端 → [API 与前端](/modules/api-web)
- 完整查询链路 → [检索问答全流程](/workflows/query)
