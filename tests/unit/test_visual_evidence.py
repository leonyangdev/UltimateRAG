"""验证 PDF 视觉证据只使用持久化定位，并能正确裁出局部页面。"""

from datetime import UTC, datetime
from io import BytesIO
from typing import cast

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image, ImageDraw

from ultimate_rag.application import VisualEvidenceService
from ultimate_rag.domain.exceptions import DocumentProcessingError, ResourceNotFoundError
from ultimate_rag.domain.models import (
    BlockType,
    Chunk,
    Document,
    DocumentAsset,
    DocumentStatus,
    SourceLocator,
)
from ultimate_rag.domain.ports import ObjectStorage, PDFPreviewRenderer
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.infrastructure.pdf_preview import PDFiumPreviewRenderer


def _sample_pdf() -> bytes:
    """创建左右颜色不同的单页 PDF，让裁剪方向与范围可以确定性验证。"""

    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 199, 199), fill="red")
    draw.rectangle((200, 0, 399, 199), fill="blue")
    buffer = BytesIO()
    image.save(buffer, format="PDF", resolution=72)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_pdfium_preview_crops_top_left_bbox_and_has_stable_etag() -> None:
    """同一可信定位应得到稳定 ETag，半页 BBox 应明显窄于完整页面。"""

    content = _sample_pdf()
    document = pdfium.PdfDocument(content)
    page = document[0]
    width, height = (float(value) for value in page.get_size())
    page.close()
    document.close()
    renderer = PDFiumPreviewRenderer(scale=1.0, padding_points=0)

    full = await renderer.render(content, page=1, bbox=None, etag_seed="doc:full")
    cropped = await renderer.render(
        content,
        page=1,
        bbox=(0.0, 0.0, width / 2, height),
        etag_seed="doc:left",
    )
    repeated = await renderer.render(
        content,
        page=1,
        bbox=(0.0, 0.0, width / 2, height),
        etag_seed="doc:left",
    )

    full_image = Image.open(BytesIO(full.content))
    cropped_image = Image.open(BytesIO(cropped.content))
    assert cropped_image.width < full_image.width
    assert cropped_image.height == full_image.height
    assert cropped.etag == repeated.etag


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "bbox", "message"),
    [
        (2, None, "页码超出"),
        (1, (100.0, 10.0, 50.0, 20.0), "区域坐标无效"),
    ],
)
async def test_pdfium_preview_rejects_invalid_persisted_location(
    page: int,
    bbox: tuple[float, float, float, float] | None,
    message: str,
) -> None:
    """事实库中的越界页码或反向 BBox 必须明确失败，不能返回错误来源截图。"""

    renderer = PDFiumPreviewRenderer(scale=1.0, padding_points=0)

    with pytest.raises(DocumentProcessingError, match=message):
        await renderer.render(_sample_pdf(), page=page, bbox=bbox, etag_seed="doc:invalid")


class _Repository:
    """只暴露视觉证据服务需要的两个事实读取方法。"""

    def __init__(self, document: Document, chunk: Chunk) -> None:
        self.document = document
        self.chunk = chunk

    async def get_chunk(self, chunk_id: str) -> Chunk:
        assert chunk_id == self.chunk.id
        return self.chunk

    async def get_document(self, document_id: str) -> Document:
        assert document_id == self.document.id
        return self.document

    async def get_document_asset(self, asset_id: str) -> DocumentAsset:
        assert asset_id == "asset-1"
        return DocumentAsset(
            id=asset_id,
            document_id=self.document.id,
            block_id="block-1",
            kind=BlockType.IMAGE,
            object_key="kb-1/doc-1/assets/asset-1.jpg",
            media_type="image/jpeg",
            filename="asset-1.jpg",
            title="Transformer 架构图",
            description="Encoder 与 Decoder",
            sha256="asset-sha",
            locator=SourceLocator(page=3),
        )


class _Storage:
    """记录服务是否按系统 Object Key 读取，而不是使用用户文件名。"""

    def __init__(self) -> None:
        self.requested_key: str | None = None

    async def get(self, object_key: str) -> bytes:
        self.requested_key = object_key
        return b"pdf"


class _Renderer:
    """记录服务传入的可信页码/BBox。"""

    def __init__(self) -> None:
        self.location: tuple[int, tuple[float, float, float, float] | None] | None = None

    async def render(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float] | None,
        etag_seed: str,
    ):
        from ultimate_rag.domain.models import DocumentPreview

        assert content == b"pdf"
        assert etag_seed
        self.location = (page, bbox)
        return DocumentPreview(content=b"jpeg", media_type="image/jpeg", etag="etag")


def _document(*, extension: str = ".pdf") -> Document:
    now = datetime.now(UTC)
    return Document(
        id="doc-1",
        knowledge_base_id="kb-1",
        filename="paper.pdf",
        mime_type="application/pdf",
        extension=extension,
        object_key="kb-1/doc-1/source.pdf",
        sha256="abc",
        status=DocumentStatus.READY,
        parser_name="pdf",
        parser_version="2.2",
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _chunk() -> Chunk:
    return Chunk(
        id="chunk-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        index=0,
        content="table",
        heading_path=(),
        token_count=1,
        locator=SourceLocator(page=8, bbox=(10.0, 20.0, 200.0, 120.0)),
    )


@pytest.mark.asyncio
async def test_visual_evidence_uses_persisted_chunk_location() -> None:
    """应用服务应从数据库取坐标、从 MinIO 取原文件，再交给本地渲染器。"""

    storage = _Storage()
    renderer = _Renderer()
    service = VisualEvidenceService(
        cast(Repository, _Repository(_document(), _chunk())),
        cast(ObjectStorage, storage),
        cast(PDFPreviewRenderer, renderer),
    )

    preview = await service.preview_chunk("chunk-1")

    assert preview.content == b"jpeg"
    assert storage.requested_key == "kb-1/doc-1/source.pdf"
    assert renderer.location == (8, (10.0, 20.0, 200.0, 120.0))


@pytest.mark.asyncio
async def test_visual_evidence_rejects_non_pdf_chunk() -> None:
    """非 PDF 不应仅因伪造页码而进入 PDFium。"""

    service = VisualEvidenceService(
        cast(Repository, _Repository(_document(extension=".md"), _chunk())),
        cast(ObjectStorage, _Storage()),
        cast(PDFPreviewRenderer, _Renderer()),
    )

    with pytest.raises(ResourceNotFoundError, match="没有可用"):
        await service.preview_chunk("chunk-1")


@pytest.mark.asyncio
async def test_visual_evidence_reads_registered_asset_object() -> None:
    """Asset 内容必须使用数据库登记的 Key，并沿用摄取期 SHA-256 作为 ETag。"""

    storage = _Storage()
    service = VisualEvidenceService(
        cast(Repository, _Repository(_document(), _chunk())),
        cast(ObjectStorage, storage),
        cast(PDFPreviewRenderer, _Renderer()),
    )

    content = await service.read_asset("asset-1")

    assert content.content == b"pdf"
    assert content.media_type == "image/jpeg"
    assert content.filename == "asset-1.jpg"
    assert content.etag == "asset-sha"
    assert storage.requested_key == "kb-1/doc-1/assets/asset-1.jpg"
