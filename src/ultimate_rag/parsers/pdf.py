"""原生 PDF 与扫描 PDF 到统一文档模型的 Parser。

模块职责：
    使用 PDFium 提取每页文本；文字量低于阈值的页面渲染为 JPEG 后交给 OCRClient。每个 Block
    都保留一基页码，使检索结果与 Citation 可以回溯原 PDF。

设计取舍：
    V2 采用“按页自动判定原生文本或 OCR”的直接流程，支持混合型 PDF，也避免为扫描文件引入
    独立任务队列。PDFium 的同步解析和渲染在线程中执行；OCR 网络调用保持异步且逐页有界。
"""

import asyncio
from dataclasses import dataclass
from io import BytesIO

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import (
    Block,
    BlockType,
    DocumentSource,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.domain.ports import OCRClient
from ultimate_rag.parsers._shared import stable_block, supports_source


@dataclass(frozen=True, slots=True)
class _PDFPage:
    """线程边界间传递的单页提取结果，不泄漏 PDFium 资源句柄。"""

    number: int
    text: str
    rendered_image: bytes | None


class PDFParser:
    """优先提取原生文本，并对低文字量页面自动执行 OCR。"""

    name = "pdf"
    version = "2.0"
    _EXTENSIONS = frozenset({".pdf"})
    _MIME_TYPES = frozenset({"application/pdf"})
    _MAX_PAGES = 500

    def __init__(
        self,
        ocr_client: OCRClient,
        *,
        native_text_threshold: int = 20,
        render_scale: float = 1.5,
    ) -> None:
        """配置扫描页判定阈值与 OCR 渲染倍率。"""

        if native_text_threshold < 0:
            raise ValueError("PDF native text threshold cannot be negative")
        if not 0.5 <= render_scale <= 3.0:
            raise ValueError("PDF render scale must be between 0.5 and 3.0")
        self._ocr_client = ocr_client
        self._native_text_threshold = native_text_threshold
        self._render_scale = render_scale

    def supports(self, source: DocumentSource) -> bool:
        """同时要求 PDF 扩展名以及 PDF 或通用二进制 MIME。"""

        return supports_source(source, self._EXTENSIONS, self._MIME_TYPES)

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """提取或 OCR 每一页，并拒绝完全没有可索引文本的 PDF。"""

        pages = await asyncio.to_thread(self._extract_pages, source.content)
        blocks: list[Block] = []
        ocr_page_count = 0
        for page in pages:
            content = page.text
            block_type = BlockType.TEXT
            if page.rendered_image is not None:
                try:
                    content = await self._ocr_client.extract_text(page.rendered_image, "image/jpeg")
                except (ValueError, RuntimeError) as exc:
                    raise InvalidDocumentError(f"PDF 第 {page.number} 页 OCR 失败：{exc}") from exc
                block_type = BlockType.IMAGE
                ocr_page_count += 1
            content = content.strip()
            if not content:
                continue
            blocks.append(
                stable_block(
                    source.document_id,
                    len(blocks),
                    block_type,
                    content,
                    SourceLocator(page=page.number),
                )
            )
        if not blocks:
            raise InvalidDocumentError("PDF 没有可索引的文本或 OCR 结果")
        return ParsedDocument(
            document_id=source.document_id,
            blocks=tuple(blocks),
            metadata={
                "parser": self.name,
                "parser_version": self.version,
                "page_count": len(pages),
                "ocr_page_count": ocr_page_count,
            },
        )

    def _extract_pages(self, content: bytes) -> list[_PDFPage]:
        """同步打开 PDF，提取文本，并只渲染需要 OCR 的页面。"""

        try:
            document = pdfium.PdfDocument(content)
            page_count = len(document)
            if page_count == 0:
                raise InvalidDocumentError("PDF 文件没有页面")
            if page_count > self._MAX_PAGES:
                raise InvalidDocumentError(f"PDF 页数不能超过 {self._MAX_PAGES} 页")
            pages: list[_PDFPage] = []
            for page_index in range(page_count):
                page = document[page_index]
                text_page = page.get_textpage()
                # bounded API 与整页默认边界等价，且避免 PDFium 对 get_text_range 默认参数
                # 的弃用警告；两者都按页面文字对象的自然顺序返回 Unicode 文本。
                text = text_page.get_text_bounded().strip()
                text_page.close()
                rendered = None
                if len(text) < self._native_text_threshold:
                    bitmap = page.render(scale=self._render_scale)
                    image = bitmap.to_pil().convert("RGB")
                    buffer = BytesIO()
                    image.save(buffer, format="JPEG", quality=85, optimize=True)
                    rendered = buffer.getvalue()
                    bitmap.close()
                page.close()
                pages.append(_PDFPage(page_index + 1, text, rendered))
            document.close()
            return pages
        except InvalidDocumentError:
            raise
        except Exception as exc:
            raise InvalidDocumentError("PDF 文件损坏、加密或无法解析") from exc
