# V2 能力与限制

这一页回答两个问题：**V2 支持什么**、**V2 不支持什么**。理解边界，才不会对项目产生错误预期。

## 1. 支持的文档格式

| 类型 | 扩展名 | 使用的 Parser | 主要来源定位 |
|---|---|---|---|
| Markdown | `.md`、`.markdown` | `MarkdownParser` | 标题路径 |
| PDF | `.pdf` | `PDFParser`（Docling + PDFium + 百炼） | 标题路径、页码、BBox |
| Word | `.docx` | `WordParser` | 标题路径 |
| Excel | `.xlsx` | `ExcelParser` | 工作表、单元格区域 |
| PowerPoint | `.pptx` | `PowerPointParser` | 幻灯片序号、标题路径 |
| HTML | `.html`、`.htm` | `HtmlParser` | 标题路径 |
| 图片 | PNG / JPEG / WEBP / TIFF / BMP | `ImageOCRParser` | 文档级定位 |

所有格式最终都转换为**同一种内部模型**（`ParsedDocument` → `Block[]`），下游切块、向量化、检索完全不感知原始格式。

## 2. PDF 的特殊处理：双路径

PDF 是最复杂的格式，V2 采用「按页判定」的两条路径：

```text
打开 PDF → 逐页探测文字量
   ├── 低文字量页（扫描页）→ 本地渲染成 JPEG → 百炼 OCR → 图片文本块
   └── 文字型页 → 本地 Docling 版面分析（Layout + TableFormer）
         ├── 恢复分栏阅读顺序
         ├── 识别标题层级、正文、列表、代码
         ├── 恢复表格单元格结构 → Markdown 表格
         ├── 记录页码 + BBox（坐标框）
         └── 图表/图片裁剪 → 百炼视觉模型理解语义
```

这样**同一份 PDF 可以混合**：前几页是文字版、后几页是扫描版，都能正确处理。

相关限制：

- 单份 PDF 最多 500 页
- 加密、损坏、空页 PDF 直接返回明确错误
- 重复的页眉页脚会被过滤，不进入索引

## 3. 异步摄取机制

V2 最大的结构变化是**上传与处理分离**：

```text
上传请求
   ↓
校验 → 存 MinIO → 在一个事务里建 Document + IngestionJob
   ↓
立即返回 202 + PENDING 状态（不等解析）
   ↓
独立 Worker 进程：领取任务 → 解析 → 切块 → 向量化 → 索引 → READY
```

要点：

- 文档与任务在**同一数据库事务**创建，杜绝「有文档没任务」的丢任务窗口
- Worker 用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取任务，支持多副本互不阻塞
- 任务带**租约 + 心跳**：Worker 崩溃后，其他 Worker 能回收任务继续处理
- 只对**临时故障**有限重试（指数退避，默认最多 3 次）；损坏文件直接 FAILED
- 文档只有全部步骤成功后才进入 `READY`，否则停在明确阶段或 `FAILED`

## 4. 检索与问答

- **Dense Retrieval**：Milvus COSINE 相似度检索，限定知识库范围
- **独立检索调试**：`POST /api/retrieval/search` 不依赖 LLM，可直接查看召回内容与分数
- **问答**：`POST /api/chat`（非流式）和 `POST /api/chat/stream`（流式）
- **引用**：答案附带 `citations`（文档、章节、页码/区域/幻灯片）
- **防幻觉**：没有召回结果时**不调用 LLM**，直接返回「根据当前知识库无法确定」

## 5. 明确的边界（V2 不做）

```text
❌ 混合检索（Dense + Sparse）、Reranker、Query Rewrite    → 属于 V3
❌ 认证、ACL、多租户、审计                                → 属于 V4
❌ DLQ 控制台、任务优先级、任务取消                        → 属于 V4
❌ 网页爬取（Html Parser 只解析上传的静态 HTML，不访问外网）
❌ Office 内嵌图片 OCR、图表数据模型、旧版格式(.doc/.xls/.ppt)
❌ 评估体系、RAGOps
❌ LangGraph / Agent / Tool Calling
```

::: warning 安全提醒
V2 没有认证与 ACL，**不应直接暴露到公网**。对公网部署前需要补齐 V4 的认证、权限、限流与审计。
:::

## 6. 配置与模型

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDING_MODEL` | `text-embedding-v4` | 文档与问题共用 |
| `EMBEDDING_DIMENSION` | `1024` | 必须与 Milvus Collection 一致 |
| `LLM_MODEL` | `qwen-plus` | 答案生成 |
| `OCR_MODEL` | `qwen3.5-ocr` | 扫描页/图片文字识别 |
| `VISION_MODEL` | `qwen3-vl-flash` | PDF 图表语义理解 |
| `CHUNK_MAX_TOKENS` | `512` | Chunk Token 预算 |
| `CHUNK_OVERLAP_TOKENS` | `64` | Chunk 重叠 Token |

完整配置项见 [配置项速查](/reference/config)。

## 下一步

- 进入架构部分 → [整体架构与分层](/architecture/overview)
