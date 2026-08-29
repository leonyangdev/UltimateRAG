"""多格式 Parser 共用的安全校验与领域映射小工具。"""

from collections.abc import Sequence
from io import BytesIO
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5
from zipfile import BadZipFile, ZipFile

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import Block, BlockType, DocumentSource, SourceLocator


def source_extension(source: DocumentSource) -> str:
    """统一按 POSIX 分隔符读取已净化文件名扩展名。"""

    return PurePosixPath(source.filename.replace("\\", "/")).suffix.lower()


def source_mime(source: DocumentSource) -> str:
    """移除 MIME 参数并转换为小写，兼容浏览器附带 charset。"""

    return source.mime_type.split(";", maxsplit=1)[0].strip().lower()


def supports_source(
    source: DocumentSource,
    extensions: frozenset[str],
    mime_types: frozenset[str],
) -> bool:
    """同时校验扩展名与 MIME，通用二进制 MIME 交给 Parser 检查实际结构。"""

    return source_extension(source) in extensions and source_mime(source) in (
        mime_types | {"application/octet-stream"}
    )


def stable_block(
    document_id: str,
    index: int,
    block_type: BlockType,
    content: str,
    locator: SourceLocator,
) -> Block:
    """使用文档、顺序、类型和内容生成可重复的 Block ID。"""

    block_id = str(
        uuid5(NAMESPACE_URL, f"{document_id}:block:{index}:{block_type.value}:{content}")
    )
    return Block(id=block_id, type=block_type, content=content, locator=locator)


def table_to_markdown(rows: Sequence[Sequence[object]]) -> str:
    """把二维单元格值转换为结构稳定、适合 Embedding 的 Markdown 表格。"""

    normalized = [
        [
            str(value).strip().replace("|", "\\|").replace("\n", " ") if value is not None else ""
            for value in row
        ]
        for row in rows
    ]
    normalized = [row for row in normalized if any(cell for cell in row)]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    padded = [row + [""] * (width - len(row)) for row in normalized]
    header = padded[0]
    separator = ["---"] * width
    body = padded[1:]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join(separator)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in body)
    return "\n".join(lines)


def validate_ooxml_archive(
    content: bytes,
    *,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = 100 * 1024 * 1024,
    max_compression_ratio: int = 200,
) -> None:
    """在 Office 库解压前拒绝损坏文件和明显 ZIP Bomb。

    OOXML 本质是 ZIP。仅限制上传压缩包大小不足以限制解压后的内存与 CPU，因此同时约束
    条目数量、总解压体积和单条目压缩比。校验只读取 Central Directory，不提取文件。
    """

    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
    except BadZipFile as exc:
        raise InvalidDocumentError("Office 文件不是有效的 OOXML 压缩包") from exc
    if len(entries) > max_entries:
        raise InvalidDocumentError("Office 文件包含过多内部条目")
    if sum(entry.file_size for entry in entries) > max_uncompressed_bytes:
        raise InvalidDocumentError("Office 文件解压后内容过大")
    for entry in entries:
        compressed = max(entry.compress_size, 1)
        if entry.file_size > 1024 * 1024 and entry.file_size / compressed > max_compression_ratio:
            raise InvalidDocumentError("Office 文件包含异常高压缩比条目")
