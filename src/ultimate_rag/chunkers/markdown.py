"""格式无关的结构、Token 与内容类型混合切块器。

最佳实践不是一个对所有文档都固定的字符窗口。本实现先尊重 Parser 已恢复的标题、页码、表格、
代码和图片边界，再使用 Token 预算切分超长内容：正文优先段落/句子，代码优先行，表格优先行并
重复表头，只有单个自然单元仍超限时才退化为带重叠的 Token 窗口。

``cl100k_base`` 不是百炼 Embedding 的官方精确 Tokenizer，而是稳定、可离线运行的预算近似器。
它用于避免中英文字符比例造成的巨大偏差；真实最佳大小仍需通过项目评估集的 Recall@k 调优。
"""

import re
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

import tiktoken
from tiktoken import Encoding

from ultimate_rag.domain.models import (
    Block,
    BlockType,
    Chunk,
    JsonValue,
    ParsedDocument,
    SourceLocator,
)


@dataclass(slots=True)
class _Section:
    """连续、同来源且切分策略兼容的 Block 聚合。"""

    locator: SourceLocator
    kind: str
    parts: list[str] = field(default_factory=list)
    block_types: set[BlockType] = field(default_factory=set)
    source_labels: set[str] = field(default_factory=set)
    extraction_methods: set[str] = field(default_factory=set)
    layout_engines: set[str] = field(default_factory=set)
    asset_ids: set[str] = field(default_factory=set)


class StructureAwareChunker:
    """按结构边界与 Token 预算生成稳定、可追溯 Chunk。"""

    _ATOMIC_KINDS = frozenset({"table", "code", "image"})

    def __init__(
        self,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        tokenizer_name: str = "cl100k_base",
    ) -> None:
        """验证预算并加载本地 Tokenizer；构造过程不访问网络。"""

        if max_tokens < 64:
            raise ValueError("max_tokens must be at least 64")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("overlap_tokens must be between 0 and max_tokens")
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens
        self._encoding: Encoding = tiktoken.get_encoding(tokenizer_name)

    async def split(self, document: ParsedDocument, knowledge_base_id: str) -> list[Chunk]:
        """先建立语义 Section，再按内容类型切分并生成稳定 ID。"""

        chunks: list[Chunk] = []
        for section_index, section in enumerate(self._build_sections(document.blocks)):
            prefix = self._heading_prefix(section.locator.heading_path)
            body_budget = self._body_budget(prefix)
            pieces = self._split_section(section, body_budget)
            # Parent ID 表示 Parser 已恢复出的一个语义 Section。检索仍命中较小 Child Chunk，
            # V3 只在最终 Context 阶段按该边界带回相邻 Child，兼顾精确召回与上下文完整性。
            parent_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{document.document_id}:parent:{section_index}:{section.kind}",
                )
            )
            for child_index, (piece, strategy) in enumerate(pieces):
                content = f"{prefix}\n\n{piece}" if prefix else piece
                token_count = self._count_tokens(content)
                if not content.strip():
                    continue
                if token_count > self._max_tokens:
                    # 所有内部路径都应遵守预算；显式断言比把超限文本交给模型后隐蔽失败更安全。
                    raise RuntimeError("Chunker produced content above configured token budget")
                index = len(chunks)
                chunk_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{document.document_id}:chunk:{index}:{content}",
                    )
                )
                block_types = list[JsonValue](sorted(value.value for value in section.block_types))
                source_labels = list[JsonValue](sorted(section.source_labels))
                extraction_methods = list[JsonValue](sorted(section.extraction_methods))
                layout_engines = list[JsonValue](sorted(section.layout_engines))
                asset_ids = list[JsonValue](sorted(section.asset_ids))
                metadata: dict[str, JsonValue] = {
                    "block_types": block_types,
                    "source_labels": source_labels,
                    "extraction_methods": extraction_methods,
                    "layout_engines": layout_engines,
                    # 图片 Section 的稳定资源 ID 随 Chunk 进入 PostgreSQL；Milvus 无需新增
                    # 二进制或 Object Key 字段，检索完成后再从事实库批量补齐资源元数据。
                    "asset_ids": asset_ids,
                    "split_strategy": strategy,
                    "tokenizer": self._encoding.name,
                    "parent_id": parent_id,
                    "parent_child_index": child_index,
                    "parent_child_count": len(pieces),
                }
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        knowledge_base_id=knowledge_base_id,
                        document_id=document.document_id,
                        index=index,
                        content=content,
                        heading_path=section.locator.heading_path,
                        token_count=token_count,
                        locator=section.locator,
                        metadata=metadata,
                    )
                )
        return chunks

    def _build_sections(self, blocks: tuple[Block, ...]) -> list[_Section]:
        """合并兼容正文，同时禁止跨页、跨 Sheet、跨 Slide 或跨语义类型。"""

        sections: list[_Section] = []
        current: _Section | None = None
        bounding_boxes: list[tuple[float, float, float, float]] = []

        def flush() -> None:
            nonlocal current, bounding_boxes
            if current is not None and any(part.strip() for part in current.parts):
                current.locator = self._merge_bbox(current.locator, bounding_boxes)
                sections.append(current)
            current = None
            bounding_boxes = []

        for block in blocks:
            if block.type == BlockType.HEADING:
                # 标题已由 Parser 写入后续 Block 的 heading_path，不重复进入正文向量。
                flush()
                continue
            content = block.content.strip()
            if not content:
                continue

            locator = block.locator or SourceLocator()
            kind = self._section_kind(block.type)
            should_start = (
                current is None
                or current.kind != kind
                or self._location_key(current.locator) != self._location_key(locator)
                or current.kind in self._ATOMIC_KINDS
            )
            if should_start:
                flush()
                current = _Section(locator=locator, kind=kind)

            assert current is not None
            current.parts.append(self._format_block(block))
            current.block_types.add(block.type)
            label = block.metadata.get("layout_label")
            if isinstance(label, str) and label:
                current.source_labels.add(label)
            extraction = block.metadata.get("extraction")
            if isinstance(extraction, str) and extraction:
                current.extraction_methods.update(extraction.split("+"))
            layout_engine = block.metadata.get("layout_engine")
            if isinstance(layout_engine, str) and layout_engine:
                current.layout_engines.add(layout_engine)
            raw_asset_ids = block.metadata.get("asset_ids")
            if isinstance(raw_asset_ids, list):
                current.asset_ids.update(
                    value for value in raw_asset_ids if isinstance(value, str) and value
                )
            if locator.bbox is not None:
                bounding_boxes.append(locator.bbox)

            # 表格、代码和图片各自形成独立 Section，避免与后续正文混合后破坏专用切分策略。
            if kind in self._ATOMIC_KINDS:
                flush()

        flush()
        return sections

    def _split_section(self, section: _Section, budget: int) -> list[tuple[str, str]]:
        """按 Section 内容类型选择确定性的切分策略。"""

        text = "\n\n".join(part for part in section.parts if part).strip()
        if not text:
            return []
        if self._count_tokens(text) <= budget:
            return [(text, "structure")]
        if section.kind == "table":
            return [(piece, "table_rows") for piece in self._split_table(text, budget)]
        if section.kind == "code":
            code_budget = max(8, budget - self._count_tokens("```\n\n```"))
            return [
                (f"```\n{piece}\n```", "code_lines")
                for piece in self._split_natural(
                    text,
                    code_budget,
                    separator="\n",
                    units=text.splitlines(),
                )
            ]

        paragraphs = [value.strip() for value in re.split(r"\n\s*\n", text) if value.strip()]
        units: list[str] = []
        for paragraph in paragraphs:
            if self._count_tokens(paragraph) <= budget:
                units.append(paragraph)
                continue
            for sentence in self._sentences(paragraph):
                if self._count_tokens(sentence) <= budget:
                    units.append(sentence)
                else:
                    units.extend(self._hard_token_split(sentence, budget))
        return [
            (piece, "paragraph_sentence_tokens")
            for piece in self._split_natural(text, budget, separator="\n\n", units=units)
        ]

    def _split_table(self, table: str, budget: int) -> list[str]:
        """按 Markdown 表格行切分，并在预算允许时重复题注、表头和二级表头。

        Docling 会把题注放在 Markdown 表格之前，旧实现因此没有识别到第二行之后的真正表头，
        最终退化为 Token 窗口并让续块丢失列语义。这里显式定位分隔行；超宽单行无法同时容纳
        表头时，采用 Docling 官方同类策略：优先保持该行完整并只对该块省略表头。
        """

        lines = [line.strip() for line in table.splitlines() if line.strip()]
        separator_index = next(
            (
                index
                for index in range(1, len(lines))
                if lines[index - 1].startswith("|") and self._is_table_separator(lines[index])
            ),
            None,
        )
        if separator_index is None:
            return self._hard_token_split(table, budget)

        header_start = separator_index - 1
        prefix_lines = [*lines[:header_start], lines[header_start], lines[separator_index]]
        data_start = separator_index + 1
        if data_start < len(lines) and self._looks_like_secondary_header(lines[data_start]):
            # Docling 用合并单元格表达多级表头时，第二级常作为首个“数据行”导出且首列为空。
            prefix_lines.append(lines[data_start])
            data_start += 1
        prefix = "\n".join(prefix_lines)
        rows = lines[data_start:]
        if not rows:
            return self._hard_token_split(prefix, budget, overlap_tokens=0)

        pieces: list[str] = []
        current_rows: list[str] = []
        for row in rows:
            row_units = (
                [row]
                if self._count_tokens(row) <= budget
                else self._hard_token_split(row, budget, overlap_tokens=0)
            )
            for unit in row_units:
                candidate = "\n".join([prefix, *current_rows, unit])
                if self._count_tokens(candidate) <= budget:
                    current_rows.append(unit)
                    continue
                if current_rows:
                    pieces.append("\n".join([prefix, *current_rows]))
                    current_rows = []

                with_header = f"{prefix}\n{unit}"
                if self._count_tokens(with_header) <= budget:
                    current_rows = [unit]
                else:
                    # 对极宽表格行，行本身比重复表头更重要；该行为与官方
                    # omit_header_on_overflow 选项一致，并且仍严格遵守 Token 上限。
                    pieces.append(unit)
        if current_rows:
            pieces.append("\n".join([prefix, *current_rows]))
        return pieces

    @staticmethod
    def _is_table_separator(line: str) -> bool:
        """严格识别 ``| --- | :---: |`` 形式的 Markdown 分隔行。"""

        cells = [cell.strip() for cell in line.strip().strip("|").split("|") if cell.strip()]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

    @staticmethod
    def _looks_like_secondary_header(line: str) -> bool:
        """识别 Docling 从跨列表头导出的首列为空二级表头。"""

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return bool(cells) and not cells[0] and any(cells[1:])

    def _split_natural(
        self,
        _text: str,
        budget: int,
        *,
        separator: str,
        units: list[str],
    ) -> list[str]:
        """贪心装入自然单元，并把上一块末尾少量单元带入下一块。"""

        normalized = [unit.strip() for unit in units if unit.strip()]
        if not normalized:
            return []
        pieces: list[str] = []
        current: list[str] = []
        for unit in normalized:
            candidate = separator.join([*current, unit])
            if not current or self._count_tokens(candidate) <= budget:
                current.append(unit)
                continue

            pieces.append(separator.join(current))
            carry = self._overlap_tail(current, separator)
            candidate_with_overlap = separator.join([*carry, unit])
            # 若重叠会挤不下完整新单元，优先保留新信息，不能为了 overlap 超出预算。
            current = (
                [*carry, unit] if self._count_tokens(candidate_with_overlap) <= budget else [unit]
            )
        if current:
            pieces.append(separator.join(current))
        return pieces

    def _overlap_tail(self, units: list[str], separator: str) -> list[str]:
        """从完整自然单元中选择不超过 overlap 预算的后缀。"""

        if self._overlap_tokens == 0:
            return []
        tail: list[str] = []
        for unit in reversed(units):
            candidate = separator.join([unit, *tail])
            if self._count_tokens(candidate) > self._overlap_tokens:
                break
            tail.insert(0, unit)
        # 不复制完整上一 Chunk，否则只有一个短单元时可能生成重复内容。
        return [] if len(tail) == len(units) else tail

    def _hard_token_split(
        self,
        text: str,
        budget: int,
        *,
        overlap_tokens: int | None = None,
    ) -> list[str]:
        """最后退路：按 Token ID 使用有限重叠窗口切分单个超长自然单元。"""

        tokens = self._encode(text)
        if len(tokens) <= budget:
            return [text]
        overlap = self._overlap_tokens if overlap_tokens is None else overlap_tokens
        overlap = min(overlap, budget - 1)
        step = budget - overlap
        return [
            self._encoding.decode(tokens[start : start + budget]).strip()
            for start in range(0, len(tokens), step)
            if tokens[start : start + budget]
        ]

    def _body_budget(self, prefix: str) -> int:
        """为重复标题预留 Token；异常长标题会先截断到总预算四分之一。"""

        if not prefix:
            return self._max_tokens
        prefix_tokens = self._count_tokens(prefix) + 1
        if prefix_tokens <= self._max_tokens // 4:
            return self._max_tokens - prefix_tokens
        # _heading_prefix 已限制标题；此分支只防御 Tokenizer 特殊编码导致的预算偏差。
        return max(32, self._max_tokens - prefix_tokens)

    def _heading_prefix(self, heading_path: tuple[str, ...]) -> str:
        """生成受限标题上下文，防止恶意超长 Heading 吞掉正文预算。"""

        if not heading_path:
            return ""
        prefix = f"章节：{' > '.join(heading_path)}"
        limit = max(16, self._max_tokens // 4)
        tokens = self._encode(prefix)
        return prefix if len(tokens) <= limit else self._encoding.decode(tokens[:limit]).strip()

    @staticmethod
    def _section_kind(block_type: BlockType) -> str:
        """把领域类型映射到少量真实有差异的切分策略。"""

        if block_type == BlockType.TABLE:
            return "table"
        if block_type == BlockType.CODE:
            return "code"
        if block_type == BlockType.IMAGE:
            return "image"
        return "prose"

    @staticmethod
    def _format_block(block: Block) -> str:
        """保留 Parser 生成的结构文本；代码围栏会在每个最终 Chunk 单独恢复。"""

        return block.content.strip()

    @staticmethod
    def _location_key(locator: SourceLocator) -> tuple[object, ...]:
        """BBox 可合并，其他来源字段变化必须形成新的 Citation 边界。"""

        return (
            locator.heading_path,
            locator.page,
            locator.sheet,
            locator.cell_range,
            locator.slide,
        )

    @staticmethod
    def _merge_bbox(
        locator: SourceLocator,
        boxes: list[tuple[float, float, float, float]],
    ) -> SourceLocator:
        """把同一页同一 Chunk 的元素 BBox 合并为可点击的最小包围框。"""

        if not boxes:
            return locator
        return SourceLocator(
            heading_path=locator.heading_path,
            page=locator.page,
            bbox=(
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ),
            sheet=locator.sheet,
            cell_range=locator.cell_range,
            slide=locator.slide,
        )

    @staticmethod
    def _sentences(paragraph: str) -> list[str]:
        """对超长段落按中英文句末和换行拆分，短段落不会进入该路径。"""

        return [
            value.strip()
            for value in re.split(r"(?<=[。！？!?；;])|(?<=[.!?])\s+|\n+", paragraph)
            if value.strip()
        ]

    def _encode(self, text: str) -> list[int]:
        """把任意不可信正文编码为 Token，特殊标记按普通文本处理。"""

        return self._encoding.encode(text, disallowed_special=())

    def _count_tokens(self, text: str) -> int:
        """返回与实际切分使用同一 Tokenizer 的精确本地计数。"""

        return len(self._encode(text))


# 兼容 V1 的公开类名，已有调用方无需迁移；新代码使用格式无关名称表达真实职责。
StructureAwareMarkdownChunker = StructureAwareChunker
