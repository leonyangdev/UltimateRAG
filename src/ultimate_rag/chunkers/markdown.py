"""结构感知 Markdown 切块模块。

模块职责：
    把 Parser 生成的 ``ParsedDocument`` 切分为适合 Embedding 和 Retrieval 的 ``Chunk``，
    同时保留标题路径、代码块边界、原始顺序和可重复计算的稳定 ID。

架构边界：
    本模块只依赖 UltimateRAG 领域模型，不解析原始 Markdown 字节、不调用 Embedding API，
    也不写入 PostgreSQL 或 Milvus。

设计背景：
    V1 先按标题路径聚合章节，只有章节超过字符预算时才继续切分。相比对整篇文档直接使用
    固定窗口，这种方式优先保留章节语义；相邻硬切窗口使用 Overlap，降低边界信息丢失。

典型调用位置：
    ``IngestionService`` 在 Markdown Parser 之后、Embedder 之前调用本模块。

注意事项 / 已知限制：
    ``max_chars`` 和 ``overlap_chars`` 是尚待 Retrieval Evaluation 校准的 V1 字符级默认值，
    不是模型 Token 上限。``token_count`` 只是观察指标，不可用于强制模型输入限制。
"""

import re
from uuid import NAMESPACE_URL, uuid5

from ultimate_rag.domain.models import BlockType, Chunk, ParsedDocument, SourceLocator


class StructureAwareChunker:
    """按来源位置、章节结构和字符预算生成可追溯的 Chunk。

    本类是无状态切块策略；构造后只保存字符预算。相同 ``document_id``、Block 顺序和内容
    会生成相同 Chunk ID，以支持摄取重试时的数据库替换与向量 Upsert。
    """

    def __init__(self, max_chars: int = 1600, overlap_chars: int = 160) -> None:
        """验证字符预算与重叠范围，防止零步长或无界切块。"""
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be between 0 and max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    async def split(self, document: ParsedDocument, knowledge_base_id: str) -> list[Chunk]:
        """先按标题聚合章节，再按字符预算切分并生成稳定 Chunk ID。

        Args:
            document: Parser 输出的统一文档，其中 Block 按原文顺序排列。
            knowledge_base_id: Chunk 所属检索隔离范围，会写入每个领域对象。

        Returns:
            按原文顺序排列的 Chunk；空文档返回空列表，由 Application Service 决定失败语义。

        复杂度:
            主流程相对 Block 数和文本字符数为 O(n)；每个内容片段只进行有限次拼接与扫描。
        """

        # 阶段 1 — Build Sections：先把连续正文 Block 聚合到各自的标题路径下。
        # 标题路径与正文一起保存，章节稍后即使拆成多个 Chunk，Citation 仍能定位原始章节。
        sections: list[tuple[SourceLocator, str]] = []
        current_locator = SourceLocator()
        current_parts: list[str] = []

        for block in document.blocks:
            block_locator = block.locator or SourceLocator()
            if block.type == BlockType.HEADING:
                # Heading Block 标记上一章节结束。标题文字已经存在于 heading_path，
                # 因此不把它重复追加到正文；最终由 _with_heading() 统一添加一次完整路径。
                self._flush_section(sections, current_locator, current_parts)
                current_locator = block_locator
                current_parts = []
                continue
            if block_locator != current_locator and current_parts:
                # V2 的页码、工作表、单元格范围或幻灯片变化都必须形成新的来源区间；
                # 不能只比较标题路径，否则 PDF 相邻页面会被合并并丢失精确引用位置。
                self._flush_section(sections, current_locator, current_parts)
                current_parts = []
            current_locator = block_locator

            # CODE Block 只保存代码正文，这里恢复通用围栏，让 Embedding 输入仍能区分代码
            # 与普通段落。V1 没有在领域模型中保存原始语言标记，因此不会尝试伪造语言名称。
            prefix = "```\n" if block.type == BlockType.CODE else ""
            suffix = "\n```" if block.type == BlockType.CODE else ""
            current_parts.append(f"{prefix}{block.content}{suffix}")

        # 最后一个章节后面没有新的 Heading 触发 Flush，循环结束时必须显式提交。
        self._flush_section(sections, current_locator, current_parts)

        # 阶段 2 — Split Sections：短章节保持完整，只有超预算章节才按段落和窗口继续切分。
        # 标题路径会写入每一段最终文本，使向量自身也携带局部结构语义。
        chunks: list[Chunk] = []
        for locator, section in sections:
            heading_path = locator.heading_path
            for piece in self._split_text(section):
                content = self._with_heading(heading_path, piece)
                index = len(chunks)

                # 阶段 3 — Build Domain Chunk：ID 包含文档、最终顺序和实际索引文本。
                # 对同一 document_id 重试相同内容会得到相同 UUID，使 Milvus Upsert 保持幂等；
                # 内容或顺序改变则生成新 ID，旧向量由 Application Service 在 Upsert 前删除。
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
                        locator=locator,
                    )
                )
        return chunks

    @staticmethod
    def _flush_section(
        sections: list[tuple[SourceLocator, str]],
        locator: SourceLocator,
        parts: list[str],
    ) -> None:
        """把当前非空章节规范化后追加到待切分列表。"""
        content = "\n\n".join(part.strip() for part in parts if part.strip()).strip()
        if content:
            sections.append((locator, content))

    def _split_text(self, text: str) -> list[str]:
        """优先把完整段落装入字符窗口，只对超长段落使用带 Overlap 的硬切分。

        普通段落之间不人为创建 Overlap，因为它们已经具有自然语义边界；只有单个段落超过
        ``max_chars`` 时，才需要重叠窗口保护落在人工切分边界附近的信息。
        """

        if len(text) <= self._max_chars:
            return [text]

        # 阶段 1：Markdown 空行是自然段落边界，先按它拆分可以优先保留完整逻辑段。
        # 单段仍然超预算时，_hard_split() 才退化为固定字符窗口。
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        pieces: list[str] = []
        current = ""
        for paragraph in paragraphs:
            for segment in self._hard_split(paragraph):
                # 阶段 2：尝试把下一段装入当前窗口。候选文本恢复段落空行，因此长度判断
                # 针对最终送入 Embedding 的实际字符串，而不是忽略分隔符的近似长度。
                candidate = f"{current}\n\n{segment}".strip() if current else segment
                if len(candidate) <= self._max_chars:
                    current = candidate
                    continue
                if current:
                    # 候选超过预算时提交旧窗口，并从当前 Segment 开始下一窗口。
                    # 超长段落已经由 _hard_split() 添加 Overlap，这里不能再次重叠，
                    # 否则同一边界会被重复计算，并可能让最终 Piece 超过 max_chars。
                    pieces.append(current)
                    current = segment
                else:
                    current = segment
        if current:
            pieces.append(current)
        return pieces

    def _hard_split(self, text: str) -> list[str]:
        """使用固定字符窗口切分无法按自然段落拆开的超长文本。

        相邻窗口共享 ``overlap_chars`` 个字符，降低实体、定义或代码语句恰好跨越边界时
        两个 Chunk 都缺少完整上下文的风险。
        """

        if len(text) <= self._max_chars:
            return [text]

        # 构造函数已经保证 ``0 <= overlap_chars < max_chars``，因此步长一定为正，
        # range() 不会进入零步长错误或无限切分。
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


# 兼容 V1 的公开类名，已有调用方无需迁移；新代码使用格式无关名称表达真实职责。
StructureAwareMarkdownChunker = StructureAwareChunker
