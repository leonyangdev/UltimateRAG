"""PDF 原文视觉证据的应用层编排。

模块职责：
    根据 Chunk ID 读取 PostgreSQL 中的可信 SourceLocator 和 Document 状态，从 MinIO 读取原
    PDF，再调用可替换渲染端口生成对话框预览；对于摄取期已抽取的图片 Asset，则按
    PostgreSQL 元数据从 MinIO 直接读取原始 JPEG。

架构边界：
    本模块决定“哪个 Chunk 可以预览”和调用顺序，不知道 PDFium、FastAPI Response 或前端图片
    组件。它不会修改文档、Chunk 或向量索引，也不会调用 OCR/Vision。

设计背景：
    表格和普通文本仍可由 MinIO 原 PDF 与 PostgreSQL Locator 按需重建预览。图片需要进入
    模型答案正文并在历史会话中稳定显示，因此摄取期额外持久化；具体取舍见 ADR-004。

典型调用位置与限制：
    ``GET /api/chunks/{chunk_id}/preview`` 调用本服务。V3 尚未实现 V4 ACL；当前访问边界与其他
    知识库 API 一致，未来增加权限时应在读取 Chunk 前统一校验。
"""

from ultimate_rag.domain.exceptions import ResourceNotFoundError
from ultimate_rag.domain.models import DocumentAssetContent, DocumentPreview, DocumentStatus
from ultimate_rag.domain.ports import ObjectStorage, PDFPreviewRenderer
from ultimate_rag.infrastructure.database.repository import Repository


class VisualEvidenceService:
    """协调事实存储和渲染端口的无状态应用服务。

    服务只接受稳定 Chunk ID，不接受客户端页码、BBox 或倍率，从而保证视觉证据与检索命中锚点
    一致并限制资源消耗。外部存储错误继续上抛给统一异常边界，非预览资源使用 404 表达。
    """

    def __init__(
        self,
        repository: Repository,
        storage: ObjectStorage,
        renderer: PDFPreviewRenderer,
    ) -> None:
        """注入事实 Repository、原文件存储与 PDF 渲染端口。

        Args:
            repository: 读取 Chunk 和 Document 业务事实的 Repository。
            storage: 按系统 Object Key 读取原文件的对象存储端口。
            renderer: 把可信 PDF 定位转换为 JPEG 的基础设施端口。
        """

        self._repository = repository
        self._storage = storage
        self._renderer = renderer

    async def preview_chunk(self, chunk_id: str) -> DocumentPreview:
        """返回命中 Chunk 对应的 PDF 原文区域。

        Args:
            chunk_id: 检索结果公开的稳定 Chunk ID。

        Returns:
            可直接映射为 HTTP 图片响应的不可变预览对象。

        Raises:
            ResourceNotFoundError: Chunk 不存在，或所属资源不是 READY PDF、没有页码。
            UltimateRAGError: MinIO 等外部依赖发生已知故障。

        Side Effects:
            只读访问 PostgreSQL 与 MinIO，并执行一次本地 PDF 栅格化；不写入任何存储。
        """

        # 阶段 1：定位只能来自 PostgreSQL Chunk 事实。若改为让浏览器传 BBox，证据将无法证明
        # 它对应真正的检索命中，还可能被任意倍率/区域请求放大资源消耗。
        chunk = await self._repository.get_chunk(chunk_id)
        document = await self._repository.get_document(chunk.document_id)
        locator = chunk.locator

        # 阶段 2：只有已完整索引的 PDF 才能成为可核验来源。扫描页允许 BBox 为空，此时渲染器
        # 返回整页；没有页码则无法确定视觉位置，按“预览资源不存在”处理。
        if (
            document.status != DocumentStatus.READY
            or document.extension.casefold() != ".pdf"
            or locator is None
            or locator.page is None
        ):
            raise ResourceNotFoundError("当前文档片段没有可用的 PDF 原文预览")

        # 阶段 3：使用 Document.object_key 读取原文件，绝不把用户文件名拼成存储路径。
        # ETag 种子绑定文档哈希和 Chunk，渲染器再加入页码/BBox/参数形成完整缓存标识。
        content = await self._storage.get(document.object_key)
        return await self._renderer.render(
            content,
            page=locator.page,
            bbox=locator.bbox,
            etag_seed=f"{document.sha256}:{chunk.id}",
        )

    async def read_asset(self, asset_id: str) -> DocumentAssetContent:
        """读取一个已完成文档的持久化图片资源。

        Args:
            asset_id: RetrievalResult 与 ``asset://`` Markdown 中公开的稳定资源 ID。

        Returns:
            可映射为 HTTP 内容响应的图片字节、MIME、文件名与强缓存标识。

        Raises:
            ResourceNotFoundError: Asset 不存在或所属文档尚未完整索引。
            UltimateRAGError: MinIO 读取失败。

        Side Effects:
            只读访问 PostgreSQL 与 MinIO；不会重新调用 OCR/Vision 或动态修改资源。
        """

        # Asset ID 只能定位数据库已经登记的系统 Object Key。接口不接受任意 Key 或文件路径，
        # 避免把对象存储变成可遍历的代理下载端点。
        asset = await self._repository.get_document_asset(asset_id)
        document = await self._repository.get_document(asset.document_id)
        if document.status is not DocumentStatus.READY:
            raise ResourceNotFoundError("当前文档资源尚不可用")

        # SHA-256 在摄取期基于实际 Asset 字节计算，可作为不可变强 ETag。文档重新解析后若
        # Asset ID 稳定但内容改变，哈希也会改变，浏览器不会继续使用旧缓存。
        content = await self._storage.get(asset.object_key)
        return DocumentAssetContent(
            content=content,
            media_type=asset.media_type,
            filename=asset.filename,
            etag=asset.sha256,
        )
