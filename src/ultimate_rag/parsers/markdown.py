"""Markdown 原始字节到统一文档领域模型的 Parser。

模块职责：
    校验 Markdown 编码与内容，使用 CommonMark Token 识别标题、正文和代码块，再映射为
    UltimateRAG 自有的 ``ParsedDocument``、``Block`` 与 ``SourceLocator``。

架构边界：
    本模块只负责语法解析与领域映射，不负责 Chunk、Embedding、对象存储或向量索引。
    ``markdown-it-py`` 的 Token 不允许离开 Parser 边界进入 Application 或 Domain。

设计背景：
    V1 使用成熟的 CommonMark Parser 而不是正则表达式解析 Markdown，避免嵌套语法和代码块
    产生脆弱边界；同时立即转换为自有领域模型，防止项目核心绑定第三方 Token 结构。

典型调用位置：
    ``IngestionService`` 根据 ``ParserRegistry`` 选择本 Parser，随后把结果交给 Chunker。

注意事项 / 已知限制：
    V1 只保留标题、普通文本和代码块，不保留链接目标、图片、表格结构或代码语言标记。
"""

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
from ultimate_rag.parsers._shared import supports_source


class MarkdownParser:
    """把 UTF-8 Markdown 转换为带稳定 ID 和标题路径的语义 Block。

    本类持有可复用但无业务状态的 ``MarkdownIt`` Parser；输出始终是 UltimateRAG 领域对象，
    调用方不需要理解 CommonMark Token 的 Open/Inline/Close 结构。
    """

    name = "markdown"
    version = "2.0"
    _EXTENSIONS = frozenset({".md", ".markdown"})
    _MIME_TYPES = frozenset({"text/markdown", "text/plain", "application/x-markdown"})

    def __init__(self) -> None:
        """创建启用 CommonMark 规则的无状态 Token 解析器。"""
        self._markdown = MarkdownIt("commonmark")

    def supports(self, source: DocumentSource) -> bool:
        """同时校验 Markdown 扩展名与常见 MIME，通用二进制由内容校验兜底。"""
        return supports_source(source, self._EXTENSIONS, self._MIME_TYPES)

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """校验并解析 Markdown 字节，返回带章节定位的统一文档。

        Args:
            source: 包含文档 ID、原始文件名、MIME 和上传字节的领域输入。

        Returns:
            只包含 V1 支持的 Heading、Text 与 Code Block 的不可变解析结果。

        Raises:
            InvalidDocumentError: 内容不是 UTF-8、为空或没有可索引文本。
        """

        try:
            # 阶段 1 — Decode：utf-8-sig 同时接受普通 UTF-8 与带 BOM 的 UTF-8，
            # 并自动移除 BOM，避免不可见字符进入第一个 Block、Chunk ID 和 Embedding 文本。
            text = source.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidDocumentError("Markdown 文件必须使用 UTF-8 编码") from exc
        if not text.strip():
            raise InvalidDocumentError("Markdown 文件不能为空")

        # 阶段 2 — Parse and Map：第三方 Parser 只承担语法识别，Token 随即转换为领域 Block。
        # 这个边界使未来替换 Markdown 库时，只需要修改本 Adapter，不影响 Chunker 和业务服务。
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
        """单次遍历 CommonMark Token，并维护每个 Block 所属的标题路径。

        ``markdown-it-py`` 使用 Open/Inline/Close Token 表示容器语法。该方法只把具有检索价值的
        标题、正文和代码映射为 Block，跳过段落与列表等不携带正文的结构 Token。
        """

        blocks: list[Block] = []

        # heading_path 表示“当前内容所在章节”的层级，例如 ["RAG", "Embedding"]。
        # _block() 会把可变 List 冻结成 Tuple，因此后续标题切换不会修改已经创建的 Locator。
        heading_path: list[str] = []
        index = 0
        position = 0
        while position < len(tokens):
            token = tokens[position]
            if token.type == "heading_open" and position + 1 < len(tokens):
                # 标题由 heading_open、inline、heading_close 三个连续 Token 表示。
                # Open Token 的 tag 提供 h1～h6 层级，紧随其后的 Inline Token 提供标题文字。
                level = int(token.tag[1])
                content = tokens[position + 1].content.strip()

                # 截断到 level - 1 后再追加新标题，可以正确退出旧章节：同级标题替换末级，
                # 更高层标题则一次退出多个下级路径，后续正文会继承新的层级快照。
                heading_path = heading_path[: level - 1]
                heading_path.append(content)
                blocks.append(
                    self._block(document_id, index, BlockType.HEADING, content, heading_path)
                )
                index += 1

                # 标题的三个 Token 已共同生成一个 Heading Block，直接整体跳过；
                # 若只前进一个位置，Inline Token 会再次被当作普通正文，导致标题重复索引。
                position += 3
                continue

            # 非标题 Token 只保留 Inline 正文和 Fence/Code Block。段落、列表等容器 Token
            # 本身没有 content，跳过它们不会丢失文字；正文会由内部 Inline Token 单独收集。
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
