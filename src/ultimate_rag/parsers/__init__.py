"""文档解析器与解析器注册表公开入口。"""

from ultimate_rag.parsers.html import HtmlParser
from ultimate_rag.parsers.image import ImageOCRParser
from ultimate_rag.parsers.markdown import MarkdownParser
from ultimate_rag.parsers.office import ExcelParser, PowerPointParser, WordParser
from ultimate_rag.parsers.pdf import PDFParser
from ultimate_rag.parsers.registry import ParserRegistry

__all__ = [
    "ExcelParser",
    "HtmlParser",
    "ImageOCRParser",
    "MarkdownParser",
    "ParserRegistry",
    "PDFParser",
    "PowerPointParser",
    "WordParser",
]
