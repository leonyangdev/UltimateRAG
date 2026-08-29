"""验证结构感知 Markdown 切块的边界、语义路径和幂等 ID。"""

from pathlib import Path

import pytest

from ultimate_rag.chunkers import StructureAwareMarkdownChunker
from ultimate_rag.domain.models import (
    Block,
    BlockType,
    DocumentSource,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.parsers import MarkdownParser

FIXTURE_CONTENT = Path("tests/fixtures/rag.md").read_bytes()


@pytest.mark.asyncio
async def test_chunker_is_structure_aware_and_stable() -> None:
    """相同文档重复切块应得到稳定 ID，并保留标题路径。"""

    source = DocumentSource("doc-1", "rag.md", "text/markdown", FIXTURE_CONTENT)
    parsed = await MarkdownParser().parse(source)
    chunker = StructureAwareMarkdownChunker(max_chars=300, overlap_chars=30)

    first = await chunker.split(parsed, "kb-1")
    second = await chunker.split(parsed, "kb-1")

    assert first
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert first[0].heading_path == ("BGE-M3",)
    assert first[0].content.startswith("章节：BGE-M3")
    assert all(chunk.token_count > 0 for chunk in first)


@pytest.mark.asyncio
async def test_chunker_splits_oversized_paragraph() -> None:
    """超长段落必须被有界拆分，避免发送超大文本到 Embedding 服务。"""

    source = DocumentSource(
        "doc-2", "large.md", "text/markdown", ("# 标题\n\n" + "知识" * 350).encode()
    )
    parsed = await MarkdownParser().parse(source)

    chunks = await StructureAwareMarkdownChunker(200, 20).split(parsed, "kb-1")

    assert len(chunks) > 1
    assert all(len(chunk.content) <= 220 for chunk in chunks)


@pytest.mark.asyncio
async def test_chunker_keeps_pdf_pages_as_separate_source_locations() -> None:
    """标题相同但页码不同的 Block 不能合并，否则 Citation 会丢失精确页码。"""

    parsed = ParsedDocument(
        document_id="pdf-1",
        blocks=(
            Block("b1", BlockType.TEXT, "第一页内容", SourceLocator(page=1)),
            Block("b2", BlockType.TEXT, "第二页内容", SourceLocator(page=2)),
        ),
    )

    chunks = await StructureAwareMarkdownChunker(200, 20).split(parsed, "kb-1")

    assert [chunk.locator.page if chunk.locator else None for chunk in chunks] == [1, 2]
