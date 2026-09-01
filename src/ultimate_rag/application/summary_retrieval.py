"""全文总结意图识别与结构化证据覆盖。

普通 RAG 的相关性 Top-K 假设问题可由少量局部片段回答。全文总结恰好相反：它需要摘要、
方法、实验和结论等互补证据。本模块使用确定性规则识别明确的总结请求，并从 PostgreSQL
Chunk 事实中按章节抽取代表块；不调用 LLM 判断路由，也不改变普通问答的高级检索链路。
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import PurePath

from ultimate_rag.domain.models import BlockType, Chunk, RetrievalIntent, RetrievalResult

_SUMMARY_PATTERNS = (
    re.compile(
        r"(?:总结|概括|归纳).{0,8}(?:文档|文件|论文|文章|报告|全文).{0,8}(?:核心|主要)?(?:内容|要点)?"
    ),
    re.compile(r"(?:文档|文件|论文|文章|报告|全文).{0,8}(?:核心内容|主要内容|内容总结|要点|主旨)"),
    re.compile(r"(?:这篇|该).{0,4}(?:论文|文章|文档|报告).{0,8}(?:讲了什么|说了什么|总结|概括)"),
    re.compile(
        r"\b(?:summari[sz]e|summary|overview|key points)\b.{0,30}"
        r"\b(?:document|paper|article|report)\b",
        re.I,
    ),
    re.compile(r"\bwhat is (?:this|the) (?:document|paper|article|report) about\b", re.I),
)

_LOW_VALUE_HEADINGS = (
    "reference",
    "bibliograph",
    "acknowledg",
    "参考文献",
    "致谢",
)

_PRIORITY_HEADINGS = (
    "abstract",
    "摘要",
    "introduction",
    "引言",
    "overview",
    "概述",
    "architecture",
    "method",
    "approach",
    "模型",
    "方法",
    "实验",
    "experiment",
    "result",
    "结果",
    "discussion",
    "讨论",
    "conclusion",
    "结论",
)


def detect_retrieval_intent(query: str) -> RetrievalIntent:
    """只把明确要求总结整份文档的查询路由到结构化覆盖策略。

    诸如“总结第三节”仍属于局部问答，继续交给相关性检索。保守路由可避免普通事实问题
    无意间加载整篇文档，造成额外延迟和上下文噪声。
    """

    normalized = " ".join(query.split())
    if any(pattern.search(normalized) for pattern in _SUMMARY_PATTERNS):
        return RetrievalIntent.DOCUMENT_SUMMARY
    return RetrievalIntent.FACT


class SummaryEvidenceSelector:
    """在有界 Token 预算内为每份文档选择跨章节代表证据。"""

    def __init__(self, *, max_chunks: int = 24, max_tokens: int = 16_000) -> None:
        if not 4 <= max_chunks <= 100:
            raise ValueError("max_chunks must be between 4 and 100")
        if max_tokens < 1024:
            raise ValueError("max_tokens must be at least 1024")
        self._max_chunks = max_chunks
        self._max_tokens = max_tokens

    def select(self, chunks: Sequence[Chunk]) -> list[RetrievalResult]:
        """先覆盖不同章节，再用高价值章节的后续块填充剩余预算。

        第一轮每个顶层章节只取一个代表块，避免长篇 Method 挤掉 Results/Conclusion；多文档时
        按文档轮转，避免第一份文档耗尽全部预算。第二轮再按章节价值和原文顺序补充细节。
        References 等尾部目录默认排除，因为它们对“核心内容”通常只有很弱的信息价值。
        """

        documents_with_headings = {chunk.document_id for chunk in chunks if chunk.heading_path}
        usable = [
            chunk
            for chunk in chunks
            if not self._is_low_value(chunk)
            and not self._is_redundant_front_matter(
                chunk,
                chunk.document_id in documents_with_headings,
            )
        ]
        if not usable:
            usable = list(chunks)

        by_document: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in usable:
            by_document[chunk.document_id].append(chunk)

        primary: list[Chunk] = []
        secondary: list[Chunk] = []
        for document_chunks in by_document.values():
            seen_sections: set[str] = set()
            for chunk in document_chunks:
                section = self._section_key(chunk)
                if section not in seen_sections:
                    primary.append(chunk)
                    seen_sections.add(section)
                else:
                    secondary.append(chunk)

        # 高价值章节先获得代表位置，但最终输出仍恢复原文顺序，便于模型理解论述结构。
        primary.sort(key=lambda chunk: (-self._priority(chunk), chunk.index))
        secondary.sort(key=lambda chunk: (-self._priority(chunk), chunk.index))
        chosen: list[Chunk] = []
        used_tokens = 0
        for chunk in (*primary, *secondary):
            if len(chosen) >= self._max_chunks:
                break
            cost = max(1, chunk.token_count)
            if chosen and used_tokens + cost > self._max_tokens:
                continue
            chosen.append(chunk)
            used_tokens += cost

        document_order = {document_id: index for index, document_id in enumerate(by_document)}
        chosen.sort(key=lambda chunk: (document_order[chunk.document_id], chunk.index))
        return [self._to_result(chunk, rank) for rank, chunk in enumerate(chosen)]

    @staticmethod
    def _section_key(chunk: Chunk) -> str:
        """优先使用最深层标题；无标题文档以固定窗口形成结构代理。

        顶层标题会把整章（例如 Transformer 的全部 3.x 小节）错误合并成一个 Section，
        随后第二轮可能被同一章的图片块填满。最深标题能让 Encoder、Multi-Head、Position
        Encoding 等互补设计各获得一次代表机会。
        """

        if chunk.heading_path:
            return chunk.heading_path[-1].strip().casefold()
        return f"__window_{chunk.index // 3}"

    @staticmethod
    def _is_low_value(chunk: Chunk) -> bool:
        heading = " / ".join(chunk.heading_path).casefold()
        return any(value in heading for value in _LOW_VALUE_HEADINGS)

    @staticmethod
    def _is_redundant_front_matter(chunk: Chunk, document_has_headings: bool) -> bool:
        """去掉已有结构文档的短版权前缀和与文件名重复的标题页。

        文件名已随每个来源进入 Context，重复标题/作者表不会帮助总结。无标题纯文本不能套用
        该规则，否则真实开头会被误删，所以只有同文档已存在标题结构时才判断。
        """

        if not document_has_headings:
            return False
        if not chunk.heading_path:
            return chunk.token_count < 64
        if len(chunk.heading_path) != 1:
            return False
        filename = chunk.metadata.get("filename")
        if not isinstance(filename, str):
            return False
        title = re.sub(r"[^\w]+", "", chunk.heading_path[0].casefold())
        stem = re.sub(r"[^\w]+", "", PurePath(filename).stem.casefold())
        return bool(title and title == stem)

    @staticmethod
    def _priority(chunk: Chunk) -> int:
        heading = " / ".join(chunk.heading_path).casefold()
        return 1 if any(value in heading for value in _PRIORITY_HEADINGS) else 0

    @staticmethod
    def _to_result(chunk: Chunk, rank: int) -> RetrievalResult:
        """把 PostgreSQL Chunk 事实转换为结构覆盖检索结果。"""

        filename = chunk.metadata.get("filename")
        raw_types = chunk.metadata.get("block_types")
        content_type_values: set[BlockType] = set()
        if isinstance(raw_types, list):
            # JSONB 是外部边界。旧版本或手工数据可能包含未知字符串，不能因为解释字段损坏
            # 就让全文总结失败；只恢复当前领域枚举明确支持的值。
            for raw_type in raw_types:
                if not isinstance(raw_type, str):
                    continue
                try:
                    content_type_values.add(BlockType(raw_type))
                except ValueError:
                    continue
        content_types = tuple(sorted(content_type_values, key=lambda value: value.value))

        return RetrievalResult(
            chunk_id=chunk.id,
            knowledge_base_id=chunk.knowledge_base_id,
            document_id=chunk.document_id,
            filename=filename if isinstance(filename, str) else "未知文档",
            content=chunk.content,
            heading_path=chunk.heading_path,
            # 分数只为保持 API 类型稳定；Trace.strategy 明确说明它不是相似度分数。
            score=1.0 / (rank + 1),
            locator=chunk.locator,
            retrieval_sources=("structural_coverage",),
            context_chunk_ids=(chunk.id,),
            content_types=content_types,
        )
