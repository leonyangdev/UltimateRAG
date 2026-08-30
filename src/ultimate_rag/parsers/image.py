"""常见图片到统一文档模型的多模态 Parser。

模块职责：
    使用 Pillow 校验真实图片格式，并融合 OCR 精确文字与 Vision 图形关系生成 Image Block。

架构边界：
    Parser 不依赖具体模型厂商。百炼调用封装在基础设施适配器中，测试可注入内存 Stub；
    Vision 是向后兼容的可选能力，只有 OCR 的既有部署仍可工作。
"""

import asyncio
import logging
from collections.abc import Awaitable
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import (
    BlockType,
    DocumentSource,
    JsonValue,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.domain.ports import OCRClient, VisionClient
from ultimate_rag.parsers._model_output import combine_ocr_and_vision, normalize_model_markdown
from ultimate_rag.parsers._shared import source_extension, source_mime, stable_block

logger = logging.getLogger(__name__)


class ImageOCRParser:
    """验证图片后融合可见文字与流程、层级、箭头等视觉关系。

    类名和 Parser 名称为兼容 V2 已有数据继续保留 ``image-ocr``；2.1 起实际能力是 OCR +
    Vision。调用方可通过 Block 的 ``extraction`` 元数据判断本次是否发生了降级。
    """

    name = "image-ocr"
    version = "2.1"
    _EXTENSION_FORMATS = {
        ".png": frozenset({"PNG"}),
        ".jpg": frozenset({"JPEG"}),
        ".jpeg": frozenset({"JPEG"}),
        ".webp": frozenset({"WEBP"}),
        ".tif": frozenset({"TIFF"}),
        ".tiff": frozenset({"TIFF"}),
        ".bmp": frozenset({"BMP"}),
    }
    _FORMAT_MIME = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
        "TIFF": "image/tiff",
        "BMP": "image/bmp",
    }

    def __init__(self, ocr_client: OCRClient, vision_client: VisionClient | None = None) -> None:
        """注入厂商无关 OCR 与可选 Vision 协议，使解析逻辑可独立测试。"""

        self._ocr_client = ocr_client
        self._vision_client = vision_client

    def supports(self, source: DocumentSource) -> bool:
        """按受支持扩展名与 image MIME 选择图片 Parser。"""

        extension = source_extension(source)
        mime_type = source_mime(source)
        return extension in self._EXTENSION_FORMATS and (
            mime_type.startswith("image/") or mime_type == "application/octet-stream"
        )

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """并发提取精确文字和视觉关系，单路失败时保留另一条可用结果。"""

        image_format = await asyncio.to_thread(self._validated_format, source)
        mime_type = self._FORMAT_MIME[image_format]
        ocr_task = self._attempt(self._ocr_client.extract_text(source.content, mime_type))
        if self._vision_client is None:
            ocr_text, ocr_error = await ocr_task
            vision_text, vision_error = "", None
        else:
            ocr_result, vision_result = await asyncio.gather(
                ocr_task,
                self._attempt(self._vision_client.describe(source.content, mime_type)),
            )
            ocr_text, ocr_error = ocr_result
            vision_text, vision_error = vision_result

        if ocr_error is not None:
            logger.warning("Image OCR extraction failed; using vision result", exc_info=ocr_error)
        if vision_error is not None:
            logger.warning(
                "Image vision extraction failed; using OCR result",
                exc_info=vision_error,
            )

        content = combine_ocr_and_vision(ocr_text, vision_text)
        if not content:
            error = ocr_error or vision_error
            if isinstance(error, ValueError):
                raise InvalidDocumentError(f"图片模型输入无效：{error}") from error
            if error is not None:
                # 网络或模型故障属于可重试外部失败，不能误标成永久损坏文件。
                raise RuntimeError("图片 OCR 与视觉理解均失败") from error
            raise InvalidDocumentError("图片没有可索引的文字或视觉语义")

        extraction_methods = []
        if normalize_model_markdown(ocr_text):
            extraction_methods.append("bailian_ocr")
        if normalize_model_markdown(vision_text):
            extraction_methods.append("bailian_vision")
        block = stable_block(
            source.document_id,
            0,
            BlockType.IMAGE,
            content,
            SourceLocator(),
            {"extraction": "+".join(extraction_methods)},
        )
        return ParsedDocument(
            document_id=source.document_id,
            blocks=(block,),
            metadata={
                "parser": self.name,
                "parser_version": self.version,
                "extraction_methods": list[JsonValue](extraction_methods),
            },
        )

    @staticmethod
    async def _attempt(operation: Awaitable[str]) -> tuple[str, Exception | None]:
        """捕获单条外部模型路径，使另一条路径可以独立降级成功。"""

        try:
            return normalize_model_markdown(await operation), None
        except Exception as exc:
            return "", exc

    def _validated_format(self, source: DocumentSource) -> str:
        """使用图片解码器校验内容，并拒绝扩展名与真实编码不一致。"""

        extension = source_extension(source)
        try:
            with Image.open(BytesIO(source.content)) as image:
                image_format = (image.format or "").upper()
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise InvalidDocumentError("图片文件损坏或格式不受支持") from exc
        if image_format not in self._EXTENSION_FORMATS[extension]:
            raise InvalidDocumentError("图片扩展名与实际编码格式不一致")
        return image_format
