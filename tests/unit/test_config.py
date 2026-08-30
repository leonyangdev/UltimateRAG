"""验证环境变量在进入应用前被转换为稳定的类型安全配置。"""

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from ultimate_rag.config import Settings


def test_settings_accepts_comma_separated_cors_origins(monkeypatch: MonkeyPatch) -> None:
    """CORS 环境变量应支持便于 Docker/Kubernetes 配置的逗号分隔写法。"""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://rag.example.com")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == [
        "http://localhost:3000",
        "https://rag.example.com",
    ]


def test_parent_context_budget_cannot_be_smaller_than_child_budget(
    monkeypatch: MonkeyPatch,
) -> None:
    """Small2Big 总预算若小于单个 Child 上限，就无法兑现配置声明的硬边界。"""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "512")
    monkeypatch.setenv("RETRIEVAL_PARENT_MAX_TOKENS", "256")

    with pytest.raises(ValidationError, match="retrieval_parent_max_tokens"):
        Settings(_env_file=None)


def test_rerank_request_budget_cannot_exceed_provider_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    """本地配置不能声称可发送超过 Qwen3 官方总请求上限的内容。"""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_MAX_REQUEST_TOKENS", "120001")

    with pytest.raises(ValidationError, match="rerank_max_request_tokens"):
        Settings(_env_file=None)
