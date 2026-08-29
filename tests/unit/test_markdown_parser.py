"""验证 Markdown 解析器的结构保留与不可信输入校验。"""

from pathlib import Path

import pytest

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import BlockType, DocumentSource
from ultimate_rag.parsers import MarkdownParser

FIXTURE_CONTENT = Path("tests/fixtures/rag.md").read_bytes()


@pytest.mark.asyncio
async def test_parser_preserves_heading_path() -> None:
    """文本块应携带从一级到当前层级的完整标题路径。"""

    source = DocumentSource(
        document_id="doc-1",
        filename="rag.md",
        mime_type="text/markdown",
        content=FIXTURE_CONTENT,
    )

    document = await MarkdownParser().parse(source)

    text_blocks = [block for block in document.blocks if block.type == BlockType.TEXT]
    assert text_blocks[0].locator is not None
    assert text_blocks[0].locator.heading_path == ("BGE-M3",)
    assert text_blocks[1].locator is not None
    assert text_blocks[1].locator.heading_path == ("BGE-M3", "V1 中的使用")


@pytest.mark.asyncio
async def test_parser_rejects_non_utf8_markdown() -> None:
    """非 UTF-8 文件应返回可理解的领域错误，而不是产生乱码。"""

    source = DocumentSource("doc-1", "bad.md", "text/markdown", b"\xff\xfe")

    with pytest.raises(InvalidDocumentError, match="UTF-8"):
        await MarkdownParser().parse(source)


@pytest.mark.asyncio
async def test_parser_rejects_empty_markdown() -> None:
    """仅含空白的 Markdown 不应进入后续切块和索引流程。"""

    source = DocumentSource("doc-1", "empty.md", "text/markdown", b"  \n")

    with pytest.raises(InvalidDocumentError, match="不能为空"):
        await MarkdownParser().parse(source)
