"""验证环境变量在进入应用前被转换为稳定的类型安全配置。"""

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
