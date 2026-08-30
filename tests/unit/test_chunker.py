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
    chunker = StructureAwareMarkdownChunker(max_tokens=160, overlap_tokens=20)

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

    chunks = await StructureAwareMarkdownChunker(100, 12).split(parsed, "kb-1")

    assert len(chunks) > 1
    assert all(chunk.token_count <= 100 for chunk in chunks)


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

    chunks = await StructureAwareMarkdownChunker(100, 12).split(parsed, "kb-1")

    assert [chunk.locator.page if chunk.locator else None for chunk in chunks] == [1, 2]


@pytest.mark.asyncio
async def test_chunker_repeats_table_header_when_splitting_rows() -> None:
    """长表格按行切分后每块都应带表头，避免召回中间行时失去列语义。"""

    rows = "\n".join(f"| 指标 {index} | {index * 10} |" for index in range(30))
    table = f"| 指标 | 数值 |\n| --- | --- |\n{rows}"
    parsed = ParsedDocument(
        document_id="table-1",
        blocks=(Block("table", BlockType.TABLE, table, SourceLocator(page=3)),),
    )

    chunks = await StructureAwareMarkdownChunker(64, 8).split(parsed, "kb-1")

    assert len(chunks) > 1
    assert all(chunk.content.startswith("| 指标 | 数值 |\n| --- | --- |") for chunk in chunks)
    assert all(chunk.token_count <= 64 for chunk in chunks)
    assert all(chunk.metadata["split_strategy"] == "table_rows" for chunk in chunks)


@pytest.mark.asyncio
async def test_chunker_repeats_docling_caption_and_multilevel_table_header() -> None:
    """Docling 题注和跨列表头都应出现在续块中，不能退化为无列语义 Token 窗口。"""

    rows = "\n".join(f"| Model {index} | {20 + index}.0 | {30 + index}.0 |" for index in range(20))
    table = (
        "Table 2: Translation quality.\n"
        "| Model | BLEU | BLEU |\n"
        "| --- | --- | --- |\n"
        "|  | EN-DE | EN-FR |\n"
        f"{rows}"
    )
    parsed = ParsedDocument(
        document_id="table-caption",
        blocks=(Block("table", BlockType.TABLE, table, SourceLocator(page=8)),),
    )

    chunks = await StructureAwareMarkdownChunker(96, 8).split(parsed, "kb-1")

    expected_prefix = (
        "Table 2: Translation quality.\n"
        "| Model | BLEU | BLEU |\n"
        "| --- | --- | --- |\n"
        "|  | EN-DE | EN-FR |"
    )
    assert len(chunks) > 1
    assert all(chunk.content.startswith(expected_prefix) for chunk in chunks)
    assert all(chunk.token_count <= 96 for chunk in chunks)


@pytest.mark.asyncio
async def test_chunker_merges_bboxes_without_crossing_page_boundary() -> None:
    """同页相邻正文可合并并扩大 BBox，不同页仍必须生成不同 Citation。"""

    parsed = ParsedDocument(
        document_id="layout-1",
        blocks=(
            Block("b1", BlockType.TEXT, "第一段", SourceLocator(page=1, bbox=(10, 20, 100, 40))),
            Block("b2", BlockType.TEXT, "第二段", SourceLocator(page=1, bbox=(12, 50, 120, 80))),
            Block("b3", BlockType.TEXT, "下一页", SourceLocator(page=2, bbox=(5, 10, 90, 30))),
        ),
    )

    chunks = await StructureAwareMarkdownChunker(100, 12).split(parsed, "kb-1")

    assert len(chunks) == 2
    assert chunks[0].locator is not None
    assert chunks[0].locator.bbox == (10, 20, 120, 80)
    assert chunks[1].locator is not None and chunks[1].locator.page == 2
