"""HTML 到统一文档模型的 Parser。

模块职责：
    清理脚本与样式内容，按照 DOM 文档顺序提取标题、正文、列表、引用、代码和表格，并把
    HTML 结构立即转换为 UltimateRAG 自有的 Block 与 SourceLocator。

安全边界：
    本 Parser 只解析上传的静态字节，不访问外部 URL、不执行 JavaScript，也不加载 CSS、图片
    或 iframe。V2 不实现网页爬取，避免 SSRF 与不受控网络访问进入文档摄取链路。
"""

import asyncio

from bs4 import BeautifulSoup, Tag

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import (
    Block,
    BlockType,
    DocumentSource,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.parsers._shared import stable_block, supports_source, table_to_markdown


class HtmlParser:
    """把静态 HTML 的可见主要内容转换为有序语义 Block。"""

    name = "html"
    version = "2.0"
    _EXTENSIONS = frozenset({".html", ".htm"})
    _MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
    _CONTENT_TAGS = frozenset(
        {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "table"}
    )

    def supports(self, source: DocumentSource) -> bool:
        """同时要求 HTML 扩展名以及 HTML 或通用二进制 MIME。"""

        return supports_source(source, self._EXTENSIONS, self._MIME_TYPES)

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """在线程中解析 HTML，避免较大 DOM 构建阻塞事件循环。"""

        return await asyncio.to_thread(self._parse_sync, source)

    def _parse_sync(self, source: DocumentSource) -> ParsedDocument:
        """清理不可见节点，并保持主要内容在 DOM 中的原始顺序。"""

        try:
            text = source.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidDocumentError("HTML 文件必须使用 UTF-8 编码") from exc
        if not text.strip():
            raise InvalidDocumentError("HTML 文件不能为空")

        try:
            soup = BeautifulSoup(text, "html.parser")
            for element in soup.find_all(["script", "style", "noscript", "template"]):
                element.decompose()
            blocks = self._extract_blocks(source.document_id, soup)
        except InvalidDocumentError:
            raise
        except Exception as exc:
            raise InvalidDocumentError("HTML 文件损坏或无法解析") from exc
        if not blocks:
            raise InvalidDocumentError("HTML 文件没有可索引的可见文本")
        return ParsedDocument(
            document_id=source.document_id,
            blocks=tuple(blocks),
            metadata={"parser": self.name, "parser_version": self.version},
        )

    def _extract_blocks(self, document_id: str, soup: BeautifulSoup) -> list[Block]:
        """提取顶层内容标签，防止同一段嵌套文本被父子标签重复索引。"""

        blocks: list[Block] = []
        heading_path: list[str] = []
        for element in soup.find_all(self._CONTENT_TAGS):
            if not isinstance(element, Tag) or self._has_content_ancestor(element):
                continue
            tag_name = element.name.lower()
            if tag_name == "table":
                content = self._table_content(element)
                block_type = BlockType.TABLE
            else:
                separator = "\n" if tag_name == "pre" else " "
                content = element.get_text(separator=separator, strip=True)
                block_type = self._block_type(tag_name)
            if not content:
                continue
            if tag_name.startswith("h") and len(tag_name) == 2 and tag_name[1].isdigit():
                level = int(tag_name[1])
                heading_path = heading_path[: level - 1] + [content]
            blocks.append(
                stable_block(
                    document_id,
                    len(blocks),
                    block_type,
                    content,
                    SourceLocator(heading_path=tuple(heading_path)),
                )
            )
        return blocks

    def _has_content_ancestor(self, element: Tag) -> bool:
        """判断元素是否已包含在另一个将被整体提取的内容节点中。"""

        parent = element.parent
        while isinstance(parent, Tag):
            if parent.name.lower() in self._CONTENT_TAGS:
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _table_content(table: Tag) -> str:
        """把 HTML 表格按行列转换为稳定 Markdown 表格。"""

        rows = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        return table_to_markdown(rows)

    @staticmethod
    def _block_type(tag_name: str) -> BlockType:
        """把少量明确 HTML 标签映射为统一 BlockType。"""

        if tag_name.startswith("h"):
            return BlockType.HEADING
        if tag_name == "li":
            return BlockType.LIST
        if tag_name == "blockquote":
            return BlockType.QUOTE
        if tag_name == "pre":
            return BlockType.CODE
        return BlockType.TEXT
