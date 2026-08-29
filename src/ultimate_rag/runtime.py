"""API 与 Worker 共用的文档处理依赖装配入口。

该模块只是 Composition Root：它知道具体适配器并把它们注入应用服务，但不包含业务流程。
集中装配可保证 API 上传校验与 Worker 真正解析使用同一 Parser Registry 和配置，避免两套进程
支持格式不一致。当前依赖数量有限，因此无需引入依赖注入框架。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ultimate_rag.application import DocumentProcessingService, IngestionService
from ultimate_rag.chunkers import StructureAwareChunker
from ultimate_rag.config import Settings
from ultimate_rag.domain.ports import Embedder, ObjectStorage, VectorStore
from ultimate_rag.embeddings import BailianEmbedder
from ultimate_rag.infrastructure.database import create_database
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.infrastructure.storage import MinioObjectStorage
from ultimate_rag.ocr import BailianOCRClient
from ultimate_rag.parsers import (
    ExcelParser,
    HtmlParser,
    ImageOCRParser,
    MarkdownParser,
    ParserRegistry,
    PDFParser,
    PowerPointParser,
    WordParser,
)
from ultimate_rag.vectorstores import MilvusVectorStore
from ultimate_rag.vision import BailianVisionClient


@dataclass(slots=True)
class ProcessingRuntime:
    """持有一个进程内可复用的文档处理依赖与应用服务。"""

    engine: AsyncEngine
    repository: Repository
    storage: ObjectStorage
    embedder: Embedder
    vector_store: VectorStore
    ingestion: IngestionService
    processor: DocumentProcessingService

    async def initialize(self) -> None:
        """在进程开放服务或领取任务前幂等准备外部存储。"""

        await self.storage.ensure_bucket()
        await self.vector_store.ensure_collection()

    async def close(self) -> None:
        """释放本进程持有的 SQLAlchemy 连接池。"""

        await self.engine.dispose()


def create_processing_runtime(settings: Settings) -> ProcessingRuntime:
    """根据集中配置装配 API 与 Worker 必须保持一致的处理组件。"""

    engine, repository = create_database(settings.database_url)
    storage = MinioObjectStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        settings.minio_secure,
    )
    embedder = BailianEmbedder(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        timeout=settings.model_timeout_seconds,
    )
    vector_store = MilvusVectorStore(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection=settings.milvus_collection,
        dimension=settings.embedding_dimension,
    )
    ocr = BailianOCRClient(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.ocr_model,
        max_image_bytes=settings.ocr_max_image_bytes,
        timeout=settings.model_timeout_seconds,
    )
    vision = BailianVisionClient(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.vision_model,
        max_image_bytes=settings.vision_max_image_bytes,
        timeout=settings.model_timeout_seconds,
    )
    registry = ParserRegistry(
        [
            MarkdownParser(),
            WordParser(),
            ExcelParser(),
            PowerPointParser(),
            HtmlParser(),
            PDFParser(
                ocr,
                vision,
                native_text_threshold=settings.pdf_native_text_threshold,
                render_scale=settings.pdf_render_scale,
                vision_concurrency=settings.pdf_vision_concurrency,
                docling_device=settings.docling_device,
                docling_num_threads=settings.docling_num_threads,
                docling_timeout=settings.docling_timeout_seconds,
                docling_images_scale=settings.docling_images_scale,
                docling_artifacts_path=settings.docling_artifacts_path,
                max_picture_bytes=settings.vision_max_image_bytes,
                max_pictures=settings.pdf_max_pictures,
                min_picture_pixels=settings.pdf_min_picture_pixels,
            ),
            ImageOCRParser(ocr),
        ]
    )
    chunker = StructureAwareChunker(
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        tokenizer_name=settings.chunk_tokenizer,
    )
    ingestion = IngestionService(
        repository=repository,
        storage=storage,
        parser_registry=registry,
        max_upload_bytes=settings.max_upload_bytes,
        job_max_attempts=settings.ingestion_job_max_attempts,
    )
    processor = DocumentProcessingService(
        repository=repository,
        storage=storage,
        parser_registry=registry,
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
    )
    return ProcessingRuntime(
        engine=engine,
        repository=repository,
        storage=storage,
        embedder=embedder,
        vector_store=vector_store,
        ingestion=ingestion,
        processor=processor,
    )
