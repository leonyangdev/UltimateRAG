"""解析器注册与解析器选择。

V2 使用显式内存注册即可满足单进程需求；本模块不实现包发现或远程插件加载。
"""

from ultimate_rag.domain.exceptions import UnsupportedDocumentTypeError
from ultimate_rag.domain.models import DocumentSource
from ultimate_rag.domain.ports import DocumentParser


class ParserRegistry:
    """维护解析器有序列表，并为文档来源选择第一个匹配实现。"""

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        """使用显式解析器列表初始化注册表，保留调用方给定的优先顺序。"""
        self._parsers = parsers or []

    def register(self, parser: DocumentParser) -> None:
        """把解析器追加到选择顺序末尾。"""
        self._parsers.append(parser)

    def resolve(self, source: DocumentSource) -> DocumentParser:
        """返回支持给定来源的解析器，否则给出明确的文档类型异常。"""
        for parser in self._parsers:
            if parser.supports(source):
                return parser
        raise UnsupportedDocumentTypeError(f"不支持的文档类型：{source.filename}")
