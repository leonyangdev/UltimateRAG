"""验证百炼 OCR 适配器的 Data URL 请求与输入边界。"""

from types import SimpleNamespace
from typing import cast

import pytest

from ultimate_rag.ocr import BailianOCRClient


class FakeCompletionsAPI:
    """记录 Chat Completions 请求并返回固定 OCR 文本。"""

    def __init__(self) -> None:
        """初始化空请求记录。"""

        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        """保存请求并模拟 OpenAI-Compatible 响应结构。"""

        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  OCR text  "))]
        )


@pytest.mark.asyncio
async def test_ocr_uses_base64_data_url_and_strips_response() -> None:
    """图片字节应编码为正确 MIME 的 Data URL，响应首尾空白应被移除。"""

    client = BailianOCRClient(
        api_key="test",
        base_url="https://example.test/v1",
        model="qwen-vl-ocr-latest",
        max_image_bytes=100,
        timeout=1,
    )
    api = FakeCompletionsAPI()
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=api)
    )

    result = await client.extract_text(b"png", "image/png")

    assert result == "OCR text"
    messages = cast(list[dict[str, object]], api.requests[0]["messages"])
    content = cast(list[dict[str, object]], messages[0]["content"])
    image_url = cast(dict[str, str], content[0]["image_url"])
    assert image_url["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_ocr_rejects_image_over_configured_limit() -> None:
    """超限图片必须在访问模型服务前失败。"""

    client = BailianOCRClient(
        api_key="test",
        base_url="https://example.test/v1",
        model="qwen-vl-ocr-latest",
        max_image_bytes=2,
        timeout=1,
    )

    with pytest.raises(ValueError, match="不能超过"):
        await client.extract_text(b"123", "image/png")
