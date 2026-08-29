"""常见图片到统一文档模型的 OCR Parser。

模块职责：
    使用 Pillow 校验真实图片格式，再调用可替换的 OCRClient 提取文字并生成 Image Block。

架构边界：
    Parser 不依赖具体模型厂商。百炼调用封装在基础设施适配器中，测试可注入内存 OCR Stub；
    图片没有可识别文字时明确失败，不生成无法检索的空 Chunk。
"""

import asyncio
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ultimate_rag.domain.exceptions import InvalidDocumentError
from ultimate_rag.domain.models import BlockType, DocumentSource, ParsedDocument, SourceLocator
from ultimate_rag.domain.ports import OCRClient
from ultimate_rag.parsers._shared import source_extension, source_mime, stable_block


class ImageOCRParser:
    """验证 PNG/JPEG/WEBP/TIFF/BMP 后通过 OCR 提取可检索文字。"""

    name = "image-ocr"
    version = "2.0"
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

    def __init__(self, ocr_client: OCRClient) -> None:
        """注入厂商无关 OCR 协议，使解析逻辑可独立测试和替换。"""

        self._ocr_client = ocr_client

    def supports(self, source: DocumentSource) -> bool:
        """按受支持扩展名与 image MIME 选择图片 Parser。"""

        extension = source_extension(source)
        mime_type = source_mime(source)
        return extension in self._EXTENSION_FORMATS and (
            mime_type.startswith("image/") or mime_type == "application/octet-stream"
        )

    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """先验证实际编码格式，再把图片提交 OCR 并构造统一结果。"""

        image_format = await asyncio.to_thread(self._validated_format, source)
        mime_type = self._FORMAT_MIME[image_format]
        try:
            content = (await self._ocr_client.extract_text(source.content, mime_type)).strip()
        except (ValueError, RuntimeError) as exc:
            raise InvalidDocumentError(f"图片 OCR 失败：{exc}") from exc
        if not content:
            raise InvalidDocumentError("图片没有可索引的识别文字")
        block = stable_block(
            source.document_id,
            0,
            BlockType.IMAGE,
            content,
            SourceLocator(),
        )
        return ParsedDocument(
            document_id=source.document_id,
            blocks=(block,),
            metadata={"parser": self.name, "parser_version": self.version},
        )

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
