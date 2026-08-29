"""验证解析器注册表按文件类型解析以及拒绝未知格式。"""

import pytest

from ultimate_rag.domain.exceptions import UnsupportedDocumentTypeError
from ultimate_rag.domain.models import DocumentSource
from ultimate_rag.parsers import MarkdownParser, ParserRegistry


def test_registry_resolves_markdown_parser() -> None:
    """Markdown 扩展名匹配应忽略大小写并返回 Markdown 解析器。"""

    source = DocumentSource("doc-1", "README.MD", "text/markdown", b"# Hello")

    parser = ParserRegistry([MarkdownParser()]).resolve(source)

    assert parser.name == "markdown"


def test_registry_rejects_unknown_type() -> None:
    """V1 范围外的文件格式必须被明确拒绝。"""

    source = DocumentSource("doc-1", "document.pdf", "application/pdf", b"pdf")

    with pytest.raises(UnsupportedDocumentTypeError):
        ParserRegistry([MarkdownParser()]).resolve(source)
