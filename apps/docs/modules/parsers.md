# Parser 解析器

代码位置：`src/ultimate_rag/parsers/`

## 1. 这一层是什么

Parser 负责把**各种原始格式**转换成**统一的领域模型**（`ParsedDocument` → `Block[]`）。这是 V2「文档智能」的核心能力，也是「新增格式不影响主流程」的关键。

## 2. 解析器全家福

```text
parsers/
├── registry.py        # ParserRegistry：按来源选择 Parser
├── _shared.py         # 公共工具（扩展名/MIME 判断、安全校验、表格转 Markdown）
├── _model_output.py   # OCR/Vision Markdown、伪表格和装饰图清理
├── markdown.py        # MarkdownParser      （.md / .markdown）
├── pdf.py             # PDFParser           （.pdf，Docling + PDFium + 百炼）
├── html.py            # HtmlParser          （.html / .htm）
├── office.py          # WordParser / ExcelParser / PowerPointParser
└── image.py           # ImageOCRParser      （PNG/JPEG/WEBP/TIFF/BMP）
```

## 3. 统一出口：所有 Parser 只做一件事

每个 Parser 实现 `DocumentParser` 端口，只有两个方法：

```python
class DocumentParser(Protocol):
    name: str
    version: str
    def supports(self, source: DocumentSource) -> bool: ...
    async def parse(self, source: DocumentSource) -> ParsedDocument: ...
```

- `supports()`：判断这个 Parser 能不能处理该来源（检查扩展名 + MIME）
- `parse()`：真正解析，输出统一 `ParsedDocument`

**第三方库对象绝不越过 Parser 边界**。例如 Markdown 用的 `markdown-it-py` Token、PDF 用的 Docling 对象、Excel 用的 openpyxl 对象，全部在 Parser 内部就转换为领域 `Block`。

## 4. ParserRegistry —— 怎么选 Parser

```python
class ParserRegistry:
    def __init__(self, parsers: list[DocumentParser] | None = None): ...

    def register(self, parser: DocumentParser): ...

    def resolve(self, source: DocumentSource) -> DocumentParser:
        for parser in self._parsers:
            if parser.supports(source):
                return parser
        raise UnsupportedDocumentTypeError(f"不支持的文档类型：{source.filename}")
```

- 按注册顺序查找第一个 `supports()` 为真的 Parser
- 找不到就抛 `UnsupportedDocumentTypeError`（→ HTTP 400）
- 在 `runtime.py` 中显式注册全部 7 种 Parser，**顺序即优先级**

## 5. 输入安全（_shared.py）

所有 Parser 都要处理**不可信输入**。公共工具提供：

- `source_extension()` / `source_mime()`：读取净化后的扩展名/MIME
- `supports_source()`：同时校验扩展名与 MIME
- `stable_block()`：生成稳定 Block ID（UUID5）
- `table_to_markdown()`：二维数组 → Markdown 表格
- `validate_ooxml_archive()`：**ZIP Bomb 防护**（Office 文件本质是 ZIP）

```python
def validate_ooxml_archive(content, *, max_entries=10_000,
                           max_uncompressed_bytes=100MB, max_compression_ratio=200):
    """在解压前拒绝损坏文件和明显 ZIP Bomb。只读 Central Directory，不提取文件。"""
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
    if len(entries) > max_entries:
        raise InvalidDocumentError("Office 文件包含过多内部条目")
    ...
```

## 6. 各格式怎么解析

### MarkdownParser

- 用 `markdown-it-py`（CommonMark 标准）解析，而不是正则
- 单次遍历 Token，维护**标题路径**（`heading_path`）
- 只保留有价值的：标题、正文、代码块

```python
# 标题处理逻辑：同级标题替换末级，更高级标题退出下级路径
heading_path = heading_path[: level - 1]
heading_path.append(content)
```

### HtmlParser

- 只解析上传的**静态 HTML**，不访问外网、不执行 JS（无 SSRF）
- 先删除 `script` / `style` / `noscript` / `template`
- 按 DOM 顺序提取标题、段落、列表、引用、代码、表格
- 用 `_has_content_ancestor()` 防止同一段文本被父子标签重复索引

### WordParser（.docx）

- 用 python-docx 读取，`Heading` 样式更新标题路径，`List` 样式映射为列表
- 表格 → Markdown 表格 Block
- 所有同步解析放 `asyncio.to_thread`，避免阻塞事件循环

### ExcelParser（.xlsx）

- 用 openpyxl `read_only` 模式（不复制整个 Workbook）
- 每 100 行形成一个表格 Block，记录 `sheet + cell_range`
- 限制：最多 100 个 Sheet、单表最多 200,000 个单元格

### PowerPointParser（.pptx）

- 每张幻灯片的标题 → `HEADING`，文本框 → `TEXT`，表格 → `TABLE`
- 记录一基 `slide` 序号

### ImageOCRParser

- 用 Pillow 验证**真实图片格式**（扩展名伪装会失败）
- 并发调用 `OCRClient` 保留精确文字，并调用 `VisionClient` 提取箭头、嵌套、流程和图表关系
- 以 `OCR 文本 / 视觉结构` 标签融合，实际路径写入 `extraction_methods`
- 清除 Markdown 包装、空表格行和重复正文的伪表格；装饰图可显式跳过
- 两条路径都无有效内容才失败，不产生空 Chunk

### PDFParser —— 最复杂

PDF 采用「按页判定」双路径（详见 [V2 能力与限制](/guide/v2-capabilities#_2-pdf-的特殊处理-双路径)）：

```text
PDFium 逐页探测文字量与栅格覆盖
  ├─ 低文字量 + 大图覆盖 → 渲染 JPEG → 百炼 OCR；稀疏结果补 Vision
  └─ 文字型页 → Docling Layout + TableFormer
        ├─ 分栏阅读顺序、标题层级、正文
        ├─ 表格 → Markdown TABLE
        ├─ 页码 + BBox
        └─ 图片裁剪 → 百炼视觉模型 → IMAGE Block + ParsedAsset
```

关键点：

- **Docling 模型延迟加载**：只在 Worker 真正处理文字型 PDF 时导入（API 上传进程不加载 Torch）
- 本地 Docling 关闭自身 OCR，避免两个 OCR 来源冲突
- 扫描判定要求低文字与大图覆盖同时成立；扫描 OCR 稀疏时补 Vision，调用有界并发
- 重复页眉页脚过滤；BBox 统一为左上角原点
- 单张附图理解失败 → 降级 OCR → 再失败跳过（不影响正文）；整页扫描 OCR 失败 → 任务失败可重试
- 图片先由 Docling 裁出 JPEG，再由百炼 Vision 生成题注补全、结构关系和可检索描述；Parser
  在原阅读位置写入 `![标题](asset://稳定ID)`，同时返回不依赖 MinIO 的 `ParsedAsset`
- Worker 将 Asset 字节写入 MinIO，将 ID、Block、题名、摘要、SHA-256、页码/BBox 写入
  PostgreSQL；Chunk 只携带 `asset_ids`，Milvus 不存二进制或内部 Object Key
- 表格保留为 Markdown `TABLE` Block；切块时按行处理并重复题注、表头和二级表头，模型可在
  答案中直接输出表格源数据
- 普通正文/表格仍可用 `page + bbox` 由 PDFium 按需裁切；已抽取图片优先读取持久化 Asset，
  避免每次打开回答都重新解析 PDF 或调用 Vision

### 6.1 为什么必须同时保留“语义文本”和“图片 Asset”

只保存图片会导致 Dense/BM25 无法理解图中内容；只保存 Vision 描述又会出现“检索到了图，
但回答无法展示图”的断层。因此 IMAGE Block 由两部分组成：

```markdown
![Transformer 架构图](asset://2b6f...)

图片解读：
图中左侧是 Encoder，右侧是 Decoder……
```

Embedding 使用整段可检索文本，`asset://` 只作为稳定资源引用。前端只有在同一 RetrievalResult
的 Asset 白名单中找到该 ID 时才加载图片；文档正文伪造的 ID 不会被渲染。

## 7. 新增一种格式要做什么（扩展点）

以新增一种 `.txt` 为例（假设产品需要）：

1. 新建 `parsers/text.py`，实现 `DocumentParser`：
   - `name = "text"`，`version = "1.0"`
   - `supports()` 检查 `.txt` 扩展名 + 文本 MIME
   - `parse()` 把文本切成 `ParsedDocument`
2. 在 `runtime.py` 的 `ParserRegistry` 列表里注册 `TextParser()`

**完成。** 切块、向量化、检索、问答全部不用改。

## 8. 解析器选型速查

| 格式 | Parser | 底层库 | 主要定位信息 |
|---|---|---|---|
| Markdown | `MarkdownParser` | markdown-it-py | 标题路径 |
| PDF | `PDFParser` | PDFium + Docling + 百炼 | 页码 + BBox + 标题 |
| Word | `WordParser` | python-docx | 标题路径 |
| Excel | `ExcelParser` | openpyxl | Sheet + 区域 |
| PPT | `PowerPointParser` | python-pptx | 幻灯片序号 |
| HTML | `HtmlParser` | BeautifulSoup | 标题路径 |
| 图片 | `ImageOCRParser` | Pillow + 百炼 OCR/Vision | 文档级 |

## 下一步

- 解析完怎么切块 → [Chunker 切块器](/modules/chunker)
- 想读 PDF 双路径的具体代码 → [核心代码导读](/workflows/code-tour)
