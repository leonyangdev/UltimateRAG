"""V1 Markdown 解析器。

解析器借助 CommonMark Token 保留标题层级和代码块，但只产出 UltimateRAG 自有领域模型。
"""

from pathlib import PurePath
from uuid import NAMESPACE_URL, uuid5

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import (
    Block,
    BlockType,
    DocumentSource,
    ParsedDocument,
    SourceLocator,
)


class MarkdownParser:
    """把 UTF-8 Markdown 转换为带标题路径的语义 Block。"""

    name = "markdown"
    version = "1.0"

    def __init__(self) -> None:
        """创建启用 CommonMark 规则的无状态 Token 解析器。"""
        self._markdown = MarkdownIt("commonmark")

    def supports(self, source: DocumentSource) -> bool:
        """V1 按安全归一化后的文件扩展名识别 Markdown。"""
        return PurePath(source.filename).suffix.lower() in {".md", ".markdown"}

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """解析 Markdown 字节并保留章节定位。

        Raises:
            InvalidDocumentError: 内容不是 UTF-8、为空或没有可索引文本。
        """
        try:
            text = source.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidDocumentError("Markdown 文件必须使用 UTF-8 编码") from exc
        if not text.strip():
            raise InvalidDocumentError("Markdown 文件不能为空")

        tokens = self._markdown.parse(text)
        blocks = self._tokens_to_blocks(source.document_id, tokens)
        if not blocks:
            raise InvalidDocumentError("Markdown 文件没有可索引的文本内容")
        return ParsedDocument(
            document_id=source.document_id,
            blocks=tuple(blocks),
            metadata={"parser": self.name, "parser_version": self.version},
        )

    def _tokens_to_blocks(self, document_id: str, tokens: list[Token]) -> list[Block]:
        """单次遍历 Token，并随标题层级变化维护当前 SourceLocator。"""
        blocks: list[Block] = []
        heading_path: list[str] = []
        index = 0
        position = 0
        while position < len(tokens):
            token = tokens[position]
            if token.type == "heading_open" and position + 1 < len(tokens):
                level = int(token.tag[1])
                content = tokens[position + 1].content.strip()
                heading_path = heading_path[: level - 1]
                heading_path.append(content)
                blocks.append(
                    self._block(document_id, index, BlockType.HEADING, content, heading_path)
                )
                index += 1
                position += 3
                continue

            block_type = self._block_type(token)
            content = token.content.strip()
            if block_type is not None and content:
                blocks.append(self._block(document_id, index, block_type, content, heading_path))
                index += 1
            position += 1
        return blocks

    @staticmethod
    def _block_type(token: Token) -> BlockType | None:
        """把 V1 关心的 CommonMark Token 映射为领域 BlockType。"""
        if token.type == "inline":
            return BlockType.TEXT
        if token.type == "fence" or token.type == "code_block":
            return BlockType.CODE
        return None

    @staticmethod
    def _block(
        document_id: str,
        index: int,
        block_type: BlockType,
        content: str,
        heading_path: list[str],
    ) -> Block:
        """用文档、顺序和内容生成稳定 Block ID，并冻结标题路径快照。"""
        block_id = str(uuid5(NAMESPACE_URL, f"{document_id}:block:{index}:{content}"))
        return Block(
            id=block_id,
            type=block_type,
            content=content,
            locator=SourceLocator(tuple(heading_path)),
        )
