"""基于 PDFium 的原文视觉证据渲染适配器。

模块职责：
    把应用层已经校验过的一基页码和可选 BBox 渲染为浏览器可展示的局部 JPEG。

架构边界：
    本模块位于 Infrastructure 层，只实现 ``PDFPreviewRenderer`` 端口。它不知道 Chunk、
    PostgreSQL、MinIO 或 HTTP，也不执行版面识别、OCR 和 Vision。

设计背景：
    PDFParser 在摄取期已把 Docling 坐标统一为左上角原点，PostgreSQL 保存该定位，MinIO 保存
    原 PDF。因此查看证据时可以确定性重建截图，无需再保存一套派生图片及其生命周期。完整取舍
    记录在 ``docs/adr/ADR-004-pdf-visual-evidence.md``。

典型调用位置：
    ``VisualEvidenceService`` 从事实存储取得原 PDF 和 SourceLocator 后调用本适配器。

重要依赖与约束：
    pypdfium2 负责打开和栅格化 PDF，Pillow 负责裁剪和 JPEG 编码。倍率、留白和质量由服务端
    固定；CPU 工作移入线程，避免阻塞 FastAPI Event Loop。无 BBox 的扫描页会返回整页。
"""

import asyncio
import hashlib
from io import BytesIO

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image

from ultimate_rag.domain.exceptions import DocumentProcessingError
from ultimate_rag.domain.models import DocumentPreview


class PDFiumPreviewRenderer:
    """将可信 PDF 定位渲染为有界 JPEG 的无状态基础设施适配器。

    实例只保存固定渲染参数，可以在 API 进程生命周期内复用。它不缓存原文件或页面，也不接受
    浏览器控制倍率，防止任意参数放大 CPU/内存开销。PDF 打开、页码和 BBox 错误会转换为
    ``DocumentProcessingError``，由统一 HTTP 异常边界处理。此时错误来自已持久化事实或原文件，
    不能误报为客户端请求参数错误。
    """

    def __init__(self, *, scale: float = 2.0, padding_points: float = 28.0) -> None:
        """设置固定渲染倍率与题注留白。

        ``2.0`` 在常见 PDF point 尺寸下能得到清晰文字且不会生成超大整页位图；28pt 留白用于
        包含 Docling Table/Picture BBox 外侧的一行题注。二者是当前验收样本的 V3 默认值，不是
        PDFium 硬限制，后续应由真实企业文档的清晰度和延迟评估校准。

        Args:
            scale: PDF point 到像素的渲染倍率，安全范围为 1～3。
            padding_points: BBox 四周额外保留的 PDF point，安全范围为 0～36。

        Raises:
            ValueError: 参数超出服务端允许的资源边界。
        """

        if not 1.0 <= scale <= 3.0:
            raise ValueError("scale must be between 1.0 and 3.0")
        if not 0 <= padding_points <= 36:
            raise ValueError("padding_points must be between 0 and 36")
        self._scale = scale
        self._padding_points = padding_points

    async def render(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float] | None,
        etag_seed: str,
    ) -> DocumentPreview:
        """在线程中完成 PDFium 栅格化并返回带缓存标识的 JPEG。

        Args:
            content: 已由 ObjectStorage 读取的完整原 PDF 字节。
            page: SourceLocator 保存的一基页码。
            bbox: 左上角原点的 ``(left, top, right, bottom)`` PDF point 坐标；为空时渲染整页。
            etag_seed: 由文档哈希和 Chunk ID 组成的稳定缓存种子，不包含用户输入。

        Returns:
            JPEG 字节、媒体类型和绑定原文/定位/渲染参数的稳定 ETag。

        Raises:
            DocumentProcessingError: PDF 无法打开、页码越界、页面尺寸或 BBox 无效。
        """

        return await asyncio.to_thread(
            self._render_sync,
            content,
            page=page,
            bbox=bbox,
            etag_seed=etag_seed,
        )

    def _render_sync(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float] | None,
        etag_seed: str,
    ) -> DocumentPreview:
        """打开受信原文件并只渲染目标页；无 BBox 的扫描页返回整页预览。"""

        # 阶段 1：PDF 仍属于不可信上传内容。PDFium 打开失败转换为可理解业务异常，
        # 但不把底层解析细节或原文件内容暴露给浏览器。
        try:
            document = pdfium.PdfDocument(content)
        except Exception as exc:
            raise DocumentProcessingError("PDF 原文件无法打开，不能生成证据预览") from exc

        try:
            # 阶段 2：页码来自数据库事实，仍需对当前原文件重新校验。文档被错误替换或旧
            # Locator 与新文件不一致时必须失败，不能悄悄返回另一页。
            if page < 1 or page > len(document):
                raise DocumentProcessingError("PDF 证据页码超出原文件范围")
            pdf_page = document[page - 1]
            try:
                page_width, page_height = (float(value) for value in pdf_page.get_size())
                image = pdf_page.render(scale=self._scale).to_pil().convert("RGB")
            finally:
                pdf_page.close()

            # 阶段 3：扫描页通常只有页码，因此保留整页；版面元素则裁到 BBox 并带回题注留白。
            if bbox is not None:
                image = self._crop(image, page_width, page_height, bbox)

            # 阶段 4：JPEG 适合论文页面和截图且浏览器原生支持。ETag 绑定所有会改变像素的
            # 输入，避免调参后浏览器继续复用旧证据图。
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
            # ETag 同时绑定原文件哈希、Chunk 定位和渲染算法版本；重建索引或更换源文件后，
            # 浏览器不会继续复用错误的局部截图。
            etag = hashlib.sha256(
                (
                    f"pdf-preview-v1:{etag_seed}:{page}:{bbox}:{self._scale}:{self._padding_points}"
                ).encode()
            ).hexdigest()
            return DocumentPreview(content=buffer.getvalue(), media_type="image/jpeg", etag=etag)
        finally:
            document.close()

    def _crop(
        self,
        image: Image.Image,
        page_width: float,
        page_height: float,
        bbox: tuple[float, float, float, float],
    ) -> Image.Image:
        """把左上角 PDF point 坐标映射到像素并夹紧在页面边界内。

        Args:
            image: PDFium 已渲染的 RGB 页面图。
            page_width: 原 PDF 页宽，单位为 point。
            page_height: 原 PDF 页高，单位为 point。
            bbox: Parser 保存的左上角原点边界框。

        Returns:
            包含固定留白的非空局部图片。

        Raises:
            DocumentProcessingError: 页面尺寸或夹紧后的 BBox 无效。
        """

        # PDF 坐标和像素尺寸不是同一单位；分别计算 X/Y 比例还能兼容非等比页面渲染结果。
        if page_width <= 0 or page_height <= 0:
            raise DocumentProcessingError("PDF 页面尺寸无效，不能生成证据预览")
        left, top, right, bottom = bbox
        padding = self._padding_points
        left = max(0.0, left - padding)
        top = max(0.0, top - padding)
        right = min(page_width, right + padding)
        bottom = min(page_height, bottom + padding)
        if right <= left or bottom <= top:
            raise DocumentProcessingError("PDF 证据区域坐标无效")

        scale_x = image.width / page_width
        scale_y = image.height / page_height
        pixel_box = (
            max(0, int(left * scale_x)),
            max(0, int(top * scale_y)),
            min(image.width, int(right * scale_x + 0.999)),
            min(image.height, int(bottom * scale_y + 0.999)),
        )
        return image.crop(pixel_box)
