"""应用配置入口。

本模块只负责把环境变量转换为类型安全的运行时配置，不创建数据库、模型客户端或其他外部资源。
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """UltimateRAG V2 的集中配置模型。

    默认值面向本地 Docker Compose 开发环境；密钥必须由环境变量或未提交的 ``.env`` 提供。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "UltimateRAG"
    environment: str = "development"
    log_level: str = "INFO"
    # NoDecode 把原始字符串交给下方校验器，避免 Settings 在校验前强制按 JSON 解码。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://192.168.3.19:3000"]
    )

    database_url: str = "postgresql+asyncpg://ultimate_rag:ultimate_rag@localhost:5432/ultimate_rag"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "ultimate_rag"
    minio_secret_key: str = "ultimate_rag_dev_secret"
    minio_secure: bool = False
    minio_bucket: str = "documents"

    milvus_uri: str = "http://localhost:19530"
    milvus_token: str | None = None
    milvus_collection: str = "knowledge_chunks"

    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_key: str
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 10
    llm_model: str = "qwen-plus"
    ocr_model: str = "qwen3.5-ocr"
    vision_model: str = "qwen3-vl-flash"
    # 百炼 Base64 OCR 接口要求原图小于 7 MB；默认留出编码和服务端校验余量。
    ocr_max_image_bytes: int = 6 * 1024 * 1024
    vision_max_image_bytes: int = 6 * 1024 * 1024
    model_timeout_seconds: float = 60.0

    max_upload_bytes: int = 10 * 1024 * 1024
    # 512 Token + 12.5% overlap 是通用起点，不是脱离评估集即可宣称的全局最优值。
    chunk_max_tokens: int = Field(default=512, ge=64, le=8192)
    chunk_overlap_tokens: int = Field(default=64, ge=0, le=2048)
    chunk_tokenizer: str = "cl100k_base"
    retrieval_top_k: int = 5
    context_max_chars: int = 12000

    # PostgreSQL 持久化队列采用有限重试与租约回收，不允许无限重试或永久 RUNNING。
    ingestion_job_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    worker_lease_seconds: int = Field(default=900, ge=30, le=7200)
    worker_heartbeat_seconds: int = Field(default=30, ge=5, le=600)
    worker_retry_delay_seconds: int = Field(default=10, ge=0, le=3600)

    pdf_native_text_threshold: int = 20
    pdf_render_scale: float = 2.0
    pdf_vision_concurrency: int = Field(default=2, ge=1, le=8)
    pdf_max_pictures: int = Field(default=20, ge=0, le=200)
    pdf_min_picture_pixels: int = Field(default=10_000, ge=1)
    docling_device: str = "cpu"
    docling_num_threads: int = Field(default=4, ge=1, le=64)
    docling_timeout_seconds: float = Field(default=600.0, ge=30.0, le=3600.0)
    docling_images_scale: float = Field(default=2.0, ge=1.0, le=3.0)
    docling_artifacts_path: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        """兼容逗号分隔环境变量和 Pydantic 原生 JSON 列表两种写法。"""
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def validate_cross_field_limits(self) -> "Settings":
        """验证需要同时比较两个字段的 Token 与 Worker 租约约束。"""

        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_max_tokens")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("worker_heartbeat_seconds must be smaller than worker_lease_seconds")
        return self


@lru_cache
def get_settings() -> Settings:
    """返回进程级只读配置，避免不同适配器重复解析环境变量。"""
    return Settings()
