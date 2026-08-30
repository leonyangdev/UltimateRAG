# Chunker 切块器

代码位置：`src/ultimate_rag/chunkers/markdown.py`

## 1. 这一层是什么

Chunker 把统一的 `ParsedDocument`（Block 序列）切分成适合 Embedding 和 Retrieval 的 **Chunk**。

**切块质量直接决定检索质量**，所以这一层值得认真理解。

## 2. 核心类：StructureAwareChunker

类名直译：**结构感知**（Structure-Aware）切块器。核心思想：

> 不是对所有文档用一个固定字符窗口，而是**先尊重 Parser 恢复的结构边界**（标题、页码、表格、代码、图片），再用 Token 预算切超长内容。

### 构造参数

```python
StructureAwareChunker(
    max_tokens=512,          # Chunk Token 预算
    overlap_tokens=64,       # Chunk 间重叠 Token
    tokenizer_name="cl100k_base",  # 本地 Token 预算近似器
)
```

::: tip 关于 Tokenizer
`cl100k_base` 是**可离线运行的预算近似器**，不是百炼 Embedding 的精确 Tokenizer。用它避免中英文按字符数估算的巨大偏差；真实最优大小仍需通过项目评估集的 Recall@k 调优。512/64 是通用基线，不是「数学最优值」。
:::

## 3. 切块流程（两个阶段）

### 阶段 1：构建语义 Section（`_build_sections`）

把连续的 Block 聚合成「Section」，规则是**同来源 + 同切分策略 + 不跨边界**：

- 标题 Block 触发 flush（标题已写入后续 Block 的 heading_path，不重复进入正文向量）
- 禁止合并跨页、跨 Sheet、跨 Slide、跨标题路径的 Block
- 表格、代码、图片各自独立成 Section（`_ATOMIC_KINDS`），不与正文混合
- 同页元素的 BBox 会被合并成最小包围框

### 阶段 2：按类型切分（`_split_section`）

Section 超预算时，**按内容类型选择不同的切分策略**：

```text
正文（prose）: 完整段落 → 再按句子 → 最后 Token 窗口
表格（table）: 按行切分，每个 Chunk 重复表头
代码（code） : 按行切分，每个 Chunk 恢复独立代码围栏
图片（image）: 独立 Chunk
```

优先级从好到坏：

```text
完整自然单元（段落/句子/表格行） > 带 overlap 的 Token 窗口（最后退路）
```

## 4. 关键代码导读

### 稳定 Chunk ID

```python
chunk_id = str(uuid5(NAMESPACE_URL, f"{document.document_id}:chunk:{index}:{content}"))
```

- 由文档 ID + 序号 + 内容生成，**幂等**（重试结果一致）
- 这是「重试不产生重复数据」的基础

### Token 硬预算

```python
if token_count > self._max_tokens:
    raise RuntimeError("Chunker produced content above configured token budget")
```

- 所有内部切分路径都必须遵守预算，**超过就显式报错**，而不是把超限文本交给模型后隐蔽失败

### 标题前缀（每个 Chunk 携带章节上下文）

```python
def _heading_prefix(self, heading_path):
    if not heading_path:
        return ""
    prefix = f"章节：{' > '.join(heading_path)}"
    limit = max(16, self._max_tokens // 4)
    tokens = self._encode(prefix)
    return prefix if len(tokens) <= limit else ...  # 截断防恶意超长标题
```

- 每个 Chunk 内容带标题前缀（如「章节：RAG > Embedding」），检索时更容易定位
- 标题最多占预算的 1/4，防止异常超长标题挤掉正文

### 表格按行切分，重复表头

- 先定位 Markdown 分隔行，而不是假设表格一定从第一行开始；Docling 放在表格前的题注会保留
- 首列为空的 EN-DE/EN-FR 等跨列二级表头也进入重复前缀
- 按行贪心装箱，每个续块在预算允许时重复「题注 + 主表头 + 二级表头」
- 极宽单行只有在连同表头会超限时才省略表头，Chunk 始终不超过硬 Token 预算

### 自然单元装入 + 尾部 overlap

```python
def _split_natural(self, _text, budget, *, separator, units):
    # 贪心装入完整单元；装不下时，把上一块末尾少量单元带入下一块
    carry = self._overlap_tail(current, separator)
    current = [*carry, unit] if ... else [unit]
```

- 只在完整自然单元之间携带重叠，**不复制完整上一 Chunk**（避免只有短单元时重复内容）
- 若重叠挤不下新单元，**优先保留新信息**，不能为了 overlap 超出预算

### 句子拆分

```python
re.split(r"(?<=[。！？!?；;])|(?<=[.!?])\s+|\n+", paragraph)
```

- 中英文句末 + 换行都能拆，长段落才进入该路径

## 5. 兼容别名

```python
# 兼容 V1 的公开类名，已有调用方无需迁移
StructureAwareMarkdownChunker = StructureAwareChunker
```

## 6. 切块策略速查表

| 内容类型 | 切分策略 | 每个 Chunk |
|---|---|---|
| 正文 | 段落 → 句子 → Token 窗口 | 带标题前缀 |
| 表格 | 按行装箱 | 重复题注与多级表头；极宽行按预算降级 |
| 代码 | 按行装箱 | 独立代码围栏 |
| 图片 | 独立 | 语义描述文本 |

来源边界（禁止跨 Chunk 合并）：

```text
heading_path / page / sheet / cell_range / slide
```

## 下一步

- 切完怎么变成向量 → [Embedding 向量化](/modules/embeddings)
- 想看完整切块代码 → [核心代码导读](/workflows/code-tour)
