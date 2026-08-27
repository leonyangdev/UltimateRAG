"""结构感知 Markdown 切块实现。

优先按标题章节聚合内容，仅在章节超过限制时进行带重叠的字符切分；字符限制是 V1 的简单策略，
估算 Token 仅用于元数据展示，不替代模型端真实 Token 校验。
"""

import re
from uuid import NAMESPACE_URL, uuid5

from ultimate_rag.domain.models import BlockType, Chunk, ParsedDocument


class StructureAwareMarkdownChunker:
    """在保留标题路径的同时生成稳定、可重复计算的 Chunk。"""

    def __init__(self, max_chars: int = 1600, overlap_chars: int = 160) -> None:
        """验证字符预算与重叠范围，防止零步长或无界切块。"""
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be between 0 and max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    async def split(self, document: ParsedDocument, knowledge_base_id: str) -> list[Chunk]:
        """将解析结果切块并通过文档 ID、序号和内容派生稳定 UUID。"""
        sections: list[tuple[tuple[str, ...], str]] = []
        current_path: tuple[str, ...] = ()
        current_parts: list[str] = []

        for block in document.blocks:
            block_path = block.locator.heading_path if block.locator else ()
            if block.type == BlockType.HEADING:
                self._flush_section(sections, current_path, current_parts)
                current_path = block_path
                current_parts = []
                continue
            if block_path != current_path and current_parts:
                self._flush_section(sections, current_path, current_parts)
                current_parts = []
            current_path = block_path
            prefix = "```\n" if block.type == BlockType.CODE else ""
            suffix = "\n```" if block.type == BlockType.CODE else ""
            current_parts.append(f"{prefix}{block.content}{suffix}")
        self._flush_section(sections, current_path, current_parts)

        chunks: list[Chunk] = []
        for heading_path, section in sections:
            for piece in self._split_text(section):
                content = self._with_heading(heading_path, piece)
                index = len(chunks)
                chunk_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{document.document_id}:chunk:{index}:{content}",
                    )
                )
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        knowledge_base_id=knowledge_base_id,
                        document_id=document.document_id,
                        index=index,
                        content=content,
                        heading_path=heading_path,
                        token_count=self._estimate_tokens(content),
                    )
                )
        return chunks

    @staticmethod
    def _flush_section(
        sections: list[tuple[tuple[str, ...], str]],
        heading_path: tuple[str, ...],
        parts: list[str],
    ) -> None:
        """把当前非空章节规范化后追加到待切分列表。"""
        content = "\n\n".join(part.strip() for part in parts if part.strip()).strip()
        if content:
            sections.append((heading_path, content))

    def _split_text(self, text: str) -> list[str]:
        """优先按段落装箱，超长段落再进入硬切分。"""
        if len(text) <= self._max_chars:
            return [text]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        pieces: list[str] = []
        current = ""
        for paragraph in paragraphs:
            for segment in self._hard_split(paragraph):
                candidate = f"{current}\n\n{segment}".strip() if current else segment
                if len(candidate) <= self._max_chars:
                    current = candidate
                    continue
                if current:
                    pieces.append(current)
                    # 超长段落的 hard split 已经包含 overlap；这里直接开始新窗口，
                    # 避免对同一边界重复叠加 overlap 并突破 max_chars。
                    current = segment
                else:
                    current = segment
        if current:
            pieces.append(current)
        return pieces

    def _hard_split(self, text: str) -> list[str]:
        """按固定字符窗口切分无法按段落拆开的超长内容。"""
        if len(text) <= self._max_chars:
            return [text]
        step = self._max_chars - self._overlap_chars
        return [text[start : start + self._max_chars] for start in range(0, len(text), step)]

    @staticmethod
    def _with_heading(heading_path: tuple[str, ...], content: str) -> str:
        """把章节路径写入 Chunk 文本，使向量本身保留结构语义。"""
        if not heading_path:
            return content
        return f"章节：{' > '.join(heading_path)}\n\n{content}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算中英文 Token 数，仅用于元数据观察而非模型限额判断。"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_words = len(re.findall(r"[A-Za-z0-9_]+", text))
        punctuation = len(re.findall(r"[^\w\s\u4e00-\u9fff]", text))
        return chinese_chars + other_words + punctuation // 2
