"""应用配置入口。

本模块只负责把环境变量转换为类型安全的运行时配置，不创建数据库、模型客户端或其他外部资源。
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """UltimateRAG V1 的集中配置模型。

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
        default_factory=lambda: ["http://localhost:3000"]
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
    model_timeout_seconds: float = 60.0

    max_upload_bytes: int = 10 * 1024 * 1024
    chunk_max_chars: int = 1600
    chunk_overlap_chars: int = 160
    retrieval_top_k: int = 5
    context_max_chars: int = 12000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        """兼容逗号分隔环境变量和 Pydantic 原生 JSON 列表两种写法。"""
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """返回进程级只读配置，避免不同适配器重复解析环境变量。"""
    return Settings()
