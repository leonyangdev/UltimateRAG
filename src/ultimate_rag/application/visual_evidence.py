"""PDF 原文视觉证据的应用层编排。

模块职责：
    根据 Chunk ID 读取 PostgreSQL 中的可信 SourceLocator 和 Document 状态，从 MinIO 读取原
    PDF，再调用可替换渲染端口生成对话框预览。

架构边界：
    本模块决定“哪个 Chunk 可以预览”和调用顺序，不知道 PDFium、FastAPI Response 或前端图片
    组件。它不会修改文档、Chunk 或向量索引，也不会调用 OCR/Vision。

设计背景：
    MinIO 原 PDF 与 PostgreSQL Locator 已能重建视觉区域。按需渲染避免为每个图片/表格维护
    第二套对象事实和跨存储补偿，具体决策见 ADR-004。

典型调用位置与限制：
    ``GET /api/chunks/{chunk_id}/preview`` 调用本服务。V3 尚未实现 V4 ACL；当前访问边界与其他
    知识库 API 一致，未来增加权限时应在读取 Chunk 前统一校验。
"""

from ultimate_rag.domain.exceptions import ResourceNotFoundError
from ultimate_rag.domain.models import DocumentPreview, DocumentStatus
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
