"""复杂 PDF 到统一文档模型的版面感知 Parser。

处理策略分为两条互补路径：

1. 文字型页面由本地 Docling 还原阅读顺序、标题层级、表格结构、图片区域与 BBox；
2. 低文字量扫描页由 PDFium 渲染后交给阿里云百炼 OCR。

Docling 的模型只在后台 Worker 首次真正处理文字型 PDF 时延迟加载，FastAPI 上传进程不会下载
或初始化版面模型。第三方文档对象全部收敛为本文件的不可变中间对象，不会越过 Parser 边界。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Protocol

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import (
    Block,
    BlockType,
    DocumentSource,
    JsonValue,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.domain.ports import OCRClient, VisionClient
from ultimate_rag.parsers._shared import stable_block, supports_source

if TYPE_CHECKING:
    from docling_core.types.doc.base import BoundingBox
    from docling_core.types.doc.document import DoclingDocument

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PDFPage:
    """PDFium 页面检查结果；只有扫描候选页携带渲染图片。"""

    number: int
    native_text: str
    rendered_image: bytes | None


@dataclass(frozen=True, slots=True)
class _LayoutElement:
    """Docling 边界内归一化后的版面元素。"""

    order: int
    page: int
    block_type: BlockType
    content: str
    locator: SourceLocator
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    image: bytes | None = None
    caption: str = ""


class _PDFLayoutAnalyzer(Protocol):
    """同步本地版面分析边界，便于 Parser 单元测试替换重量模型。"""

    def analyze(
        self,
        content: bytes,
        filename: str,
        skipped_pages: frozenset[int],
    ) -> list[_LayoutElement]:
        """按自然阅读顺序返回非扫描页面的结构元素。"""
        ...


class DoclingPDFLayoutAnalyzer:
    """使用 Docling Layout + TableFormer 的本地 PDF 版面分析器。"""

    _SKIPPED_LABELS = frozenset({"page_header", "page_footer"})

    def __init__(
        self,
        *,
        device: str,
        num_threads: int,
        document_timeout: float,
        images_scale: float,
        max_picture_bytes: int,
        max_pictures: int,
        min_picture_pixels: int,
        artifacts_path: str | None = None,
    ) -> None:
        """保存模型、图片和资源边界；构造时不导入模型运行时。"""

        self._device = device
        self._num_threads = num_threads
        self._document_timeout = document_timeout
        self._images_scale = images_scale
        self._max_picture_bytes = max_picture_bytes
        self._max_pictures = max_pictures
        self._min_picture_pixels = min_picture_pixels
        self._artifacts_path = artifacts_path

    def analyze(
        self,
        content: bytes,
        filename: str,
        skipped_pages: frozenset[int],
    ) -> list[_LayoutElement]:
        """执行本地版面推理，并在适配器边界内导出文本、表格和图片。"""

        # 重量依赖在 Worker 真正收到文字型 PDF 后才导入。这样 API 进程能够快速启动，
        # 普通 Markdown/Office 任务也不会承担 Torch 与模型初始化成本。
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            HeadingHierarchyOptions,
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc.items.picture.picture import PictureItem
        from docling_core.types.doc.items.table.table import TableItem
        from docling_core.types.doc.items.text import SectionHeaderItem, TextItem
        from docling_core.types.io import DocumentStream

        try:
            pipeline_options = PdfPipelineOptions(
                # 扫描 OCR 明确交给百炼；本地模型只负责确定性的 Layout 与表格结构。
                do_ocr=False,
                do_table_structure=True,
                table_structure_options=TableStructureOptions(
                    do_cell_matching=True,
                    mode=TableFormerMode.ACCURATE,
                ),
                generate_picture_images=True,
                images_scale=self._images_scale,
                generate_parsed_pages=True,
                heading_hierarchy_options=HeadingHierarchyOptions(enabled=True),
                document_timeout=self._document_timeout,
                accelerator_options=AcceleratorOptions(
                    device=self._device,
                    num_threads=self._num_threads,
                ),
                artifacts_path=self._artifacts_path,
            )
            converter = DocumentConverter(
                allowed_formats=[InputFormat.PDF],
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                },
            )
            result = converter.convert(
                DocumentStream(name=filename, stream=BytesIO(content)),
                raises_on_error=True,
                max_num_pages=PDFParser._MAX_PAGES,
                max_file_size=len(content),
            )
            document = result.document
        except Exception as exc:
            # PDFium 已在调用本方法前验证文件可打开。此处失败更可能是模型下载、资源或推理
            # 故障，因此保留为可重试 RuntimeError，而不是错误归类为不可重试的损坏文件。
            raise RuntimeError("PDF 本地版面分析失败") from exc

        elements: list[_LayoutElement] = []
        heading_path: list[str] = []
        picture_count = 0
        for order, (item, _level) in enumerate(document.iterate_items()):
            if not isinstance(item, (PictureItem, TableItem, TextItem)):
                continue
            if not getattr(item, "prov", None):
                continue
            provenance = item.prov[0]
            page = int(provenance.page_no)
            if page in skipped_pages:
                continue

            label = str(item.label.value)
            if label in self._SKIPPED_LABELS:
                # 重复页眉页脚通常会污染召回；页码仍由 SourceLocator 保留，不依赖正文中的页脚。
                continue
            locator = SourceLocator(
                heading_path=tuple(heading_path),
                page=page,
                bbox=self._top_left_bbox(document, page, provenance.bbox),
            )
            metadata: dict[str, JsonValue] = {
                "layout_label": label,
                "reading_order": order,
                "layout_engine": "docling",
            }

            if isinstance(item, SectionHeaderItem) or label in {"title", "section_header"}:
                text = str(getattr(item, "text", "")).strip()
                if not text:
                    continue
                level = max(1, min(int(getattr(item, "level", 1)), 6))
                heading_path = heading_path[: level - 1]
                heading_path.append(text)
                locator = SourceLocator(
                    heading_path=tuple(heading_path),
                    page=page,
                    bbox=locator.bbox,
                )
                elements.append(
                    _LayoutElement(
                        order,
                        page,
                        BlockType.HEADING,
                        text,
                        locator,
                        {**metadata, "heading_level": level},
                    )
                )
                continue

            if isinstance(item, TableItem):
                table = item.export_to_markdown(document).strip()
                if table:
                    elements.append(
                        _LayoutElement(
                            order,
                            page,
                            BlockType.TABLE,
                            table,
                            locator,
                            metadata,
                            caption=item.caption_text(document).strip(),
                        )
                    )
                continue

            if isinstance(item, PictureItem):
                if picture_count >= self._max_pictures:
                    continue
                image = item.get_image(document)
                if image is None or image.width * image.height < self._min_picture_pixels:
                    continue
                encoded = self._encode_picture(image)
                if not encoded:
                    continue
                picture_count += 1
                elements.append(
                    _LayoutElement(
                        order,
                        page,
                        BlockType.IMAGE,
                        "",
                        locator,
                        metadata,
                        image=encoded,
                        caption=item.caption_text(document).strip(),
                    )
                )
                continue

            if isinstance(item, TextItem):
                text = item.text.strip()
                if not text:
                    continue
                block_type = {
                    "code": BlockType.CODE,
                    "list_item": BlockType.LIST,
                    "footnote": BlockType.QUOTE,
                }.get(label, BlockType.TEXT)
                elements.append(_LayoutElement(order, page, block_type, text, locator, metadata))

        return elements

    @staticmethod
    def _top_left_bbox(
        document: DoclingDocument,
        page: int,
        bbox: BoundingBox,
    ) -> tuple[float, float, float, float]:
        """把 Docling BBox 统一为左上角原点，并返回可 JSON 化坐标。"""

        # Docling 页对象与 BoundingBox 都在重量依赖内部，使用局部动态属性可以避免模块导入时
        # 加载整个运行时；这段代码只接受 Docling 自己刚产生的受控对象。
        page_height = float(document.pages[page].size.height)
        normalized = bbox.to_top_left_origin(page_height)
        left, top, right, bottom = normalized.as_tuple()
        return (float(left), float(top), float(right), float(bottom))

    def _encode_picture(self, image: Image.Image) -> bytes:
        """把版面裁图压缩到百炼 Base64 上限以内，避免上传整页无界位图。"""

        picture = image.convert("RGB")
        picture.thumbnail((2048, 2048))
        for quality in (88, 76, 64, 52):
            buffer = BytesIO()
            picture.save(buffer, format="JPEG", quality=quality, optimize=True)
            value = buffer.getvalue()
            if len(value) <= self._max_picture_bytes:
                return value
            # 单纯降低质量仍超限时同步缩小像素；循环有固定次数，不会无限处理恶意大图。
            picture.thumbnail((max(1, picture.width * 3 // 4), max(1, picture.height * 3 // 4)))
        return b""


class PDFParser:
    """组合本地版面分析、百炼扫描 OCR 与嵌入图片理解。"""

    name = "pdf-docling"
    version = "2.1"
    _EXTENSIONS = frozenset({".pdf"})
    _MIME_TYPES = frozenset({"application/pdf"})
    _MAX_PAGES = 500

    def __init__(
        self,
        ocr_client: OCRClient,
        vision_client: VisionClient | None = None,
        *,
        native_text_threshold: int = 20,
        render_scale: float = 2.0,
        vision_concurrency: int = 2,
        layout_analyzer: _PDFLayoutAnalyzer | None = None,
        docling_device: str = "auto",
        docling_num_threads: int = 4,
        docling_timeout: float = 600.0,
        docling_images_scale: float = 2.0,
        docling_artifacts_path: str | None = None,
        max_picture_bytes: int = 6 * 1024 * 1024,
        max_pictures: int = 20,
        min_picture_pixels: int = 10_000,
    ) -> None:
        """配置扫描判定、并发和本地 Docling 资源上限。"""

        if native_text_threshold < 0:
            raise ValueError("PDF native text threshold cannot be negative")
        if not 0.5 <= render_scale <= 3.0:
            raise ValueError("PDF render scale must be between 0.5 and 3.0")
        if vision_concurrency < 1:
            raise ValueError("PDF vision concurrency must be at least 1")
        self._ocr_client = ocr_client
        self._vision_client = vision_client
        self._native_text_threshold = native_text_threshold
        self._render_scale = render_scale
        self._vision_concurrency = vision_concurrency
        self._layout_analyzer = layout_analyzer or DoclingPDFLayoutAnalyzer(
            device=docling_device,
            num_threads=docling_num_threads,
            document_timeout=docling_timeout,
            images_scale=docling_images_scale,
            max_picture_bytes=max_picture_bytes,
            max_pictures=max_pictures,
            min_picture_pixels=min_picture_pixels,
            artifacts_path=docling_artifacts_path,
        )

    def supports(self, source: DocumentSource) -> bool:
        """同时要求 PDF 扩展名以及 PDF 或通用二进制 MIME。"""

        return supports_source(source, self._EXTENSIONS, self._MIME_TYPES)

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """按页选择布局或 OCR 路径，并保留阅读顺序、页码与 BBox。"""

        pages = await asyncio.to_thread(self._inspect_pages, source.content)
        scanned_pages = frozenset(page.number for page in pages if page.rendered_image is not None)
        layout_elements: list[_LayoutElement] = []
        if len(scanned_pages) < len(pages):
            layout_elements = await asyncio.to_thread(
                self._layout_analyzer.analyze,
                source.content,
                source.filename,
                scanned_pages,
            )

        resolved_layout = await self._resolve_layout_images(layout_elements)
        elements_by_page: dict[int, list[_LayoutElement]] = {}
        for element in resolved_layout:
            elements_by_page.setdefault(element.page, []).append(element)

        ocr_results = await self._ocr_scanned_pages(pages)
        blocks: list[Block] = []
        fallback_page_count = 0
        for page in pages:
            page_elements = sorted(
                elements_by_page.get(page.number, []), key=lambda item: item.order
            )
            if page.number in ocr_results:
                text = ocr_results[page.number].strip()
                if text:
                    page_elements = [
                        _LayoutElement(
                            0,
                            page.number,
                            BlockType.IMAGE,
                            text,
                            SourceLocator(page=page.number),
                            {"extraction": "bailian_page_ocr"},
                        )
                    ]
            elif not page_elements and page.native_text.strip():
                # 极少数 PDF 能被 PDFium 提取文字但版面模型没有产出元素。保留按页文本比静默
                # 丢页更安全，同时通过 metadata 让评估可以识别这一降级路径。
                fallback_page_count += 1
                page_elements = [
                    _LayoutElement(
                        0,
                        page.number,
                        BlockType.TEXT,
                        page.native_text.strip(),
                        SourceLocator(page=page.number),
                        {"extraction": "pdfium_text_fallback"},
                    )
                ]

            for element in page_elements:
                content = element.content.strip()
                if not content:
                    continue
                blocks.append(
                    stable_block(
                        source.document_id,
                        len(blocks),
                        element.block_type,
                        content,
                        element.locator,
                        element.metadata,
                    )
                )

        if not blocks:
            raise InvalidDocumentError("PDF 没有可索引的文本、表格、图片语义或 OCR 结果")
        return ParsedDocument(
            document_id=source.document_id,
            blocks=tuple(blocks),
            metadata={
                "parser": self.name,
                "parser_version": self.version,
                "page_count": len(pages),
                "ocr_page_count": len(ocr_results),
                "table_count": sum(block.type == BlockType.TABLE for block in blocks),
                "image_count": sum(block.type == BlockType.IMAGE for block in blocks),
                "fallback_page_count": fallback_page_count,
                "layout_engine": "docling",
            },
        )

    async def _resolve_layout_images(
        self,
        elements: list[_LayoutElement],
    ) -> list[_LayoutElement]:
        """有界并发理解 Docling 裁出的图片，并保持原始元素顺序。"""

        semaphore = asyncio.Semaphore(self._vision_concurrency)

        async def resolve(element: _LayoutElement) -> _LayoutElement | None:
            if element.image is None:
                return element
            async with semaphore:
                content = ""
                if self._vision_client is not None:
                    try:
                        content = await self._vision_client.describe(
                            element.image,
                            "image/jpeg",
                            element.caption,
                        )
                    except Exception:
                        # 单个附图理解失败不应使正文完全不可用；继续尝试 OCR，完整异常只写日志。
                        logger.warning(
                            "PDF embedded-image vision analysis failed",
                            extra={"page": element.page},
                            exc_info=True,
                        )
                if not content:
                    try:
                        content = await self._ocr_client.extract_text(
                            element.image,
                            "image/jpeg",
                        )
                    except Exception:
                        logger.warning(
                            "PDF embedded-image OCR fallback failed",
                            extra={"page": element.page},
                            exc_info=True,
                        )
                        return None
                return _LayoutElement(
                    element.order,
                    element.page,
                    element.block_type,
                    content,
                    element.locator,
                    {**element.metadata, "extraction": "bailian_vision"},
                    caption=element.caption,
                )

        values = await asyncio.gather(*(resolve(element) for element in elements))
        return [value for value in values if value is not None]

    async def _ocr_scanned_pages(self, pages: list[_PDFPage]) -> dict[int, str]:
        """有界并发 OCR 扫描页；非法渲染不可重试，服务故障交给 Worker 重试。"""

        semaphore = asyncio.Semaphore(self._vision_concurrency)

        async def recognize(page: _PDFPage) -> tuple[int, str] | None:
            if page.rendered_image is None:
                return None
            async with semaphore:
                try:
                    text = await self._ocr_client.extract_text(page.rendered_image, "image/jpeg")
                except ValueError as exc:
                    raise InvalidDocumentError(
                        f"PDF 第 {page.number} 页 OCR 输入无效：{exc}"
                    ) from exc
                return page.number, text

        values = await asyncio.gather(*(recognize(page) for page in pages))
        return {value[0]: value[1] for value in values if value is not None}

    def _inspect_pages(self, content: bytes) -> list[_PDFPage]:
        """用 PDFium 验证 PDF、识别低文本页，并只渲染需要百炼 OCR 的页面。"""

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
                native_text = text_page.get_text_bounded().strip()
                text_page.close()
                rendered = None
                visible_character_count = len("".join(native_text.split()))
                if visible_character_count < self._native_text_threshold:
                    bitmap = page.render(scale=self._render_scale)
                    image = bitmap.to_pil().convert("RGB")
                    buffer = BytesIO()
                    image.save(buffer, format="JPEG", quality=88, optimize=True)
                    rendered = buffer.getvalue()
                    bitmap.close()
                page.close()
                pages.append(_PDFPage(page_index + 1, native_text, rendered))
            document.close()
            return pages
        except InvalidDocumentError:
            raise
        except Exception as exc:
            raise InvalidDocumentError("PDF 文件损坏、加密或无法解析") from exc
