"""验证百炼 LLM 适配器使用真实供应商增量并可靠关闭响应流。"""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ultimate_rag.generation import BailianLLMClient


class FakeResponseStream:
    """模拟包含控制 Chunk 与文本 Chunk 的 OpenAI-Compatible 异步流。"""

    def __init__(self) -> None:
        self.is_closed = False

    async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="第一段"))])
        yield SimpleNamespace(choices=[])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="第二段"))])

    async def close(self) -> None:
        """记录适配器是否释放了底层 HTTP Stream。"""
        self.is_closed = True


class FakeCompletionsAPI:
    """记录 Chat Completions 参数并返回测试异步流。"""

    def __init__(self, response: FakeResponseStream) -> None:
        self.response = response
        self.request: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> FakeResponseStream:
        self.request = kwargs
        return self.response


@pytest.mark.asyncio
async def test_llm_stream_skips_control_chunks_and_closes_response() -> None:
    """适配器只应产出非空文本，并在流正常完成后释放响应连接。"""

    client = BailianLLMClient(
        api_key="test",
        base_url="https://example.test/v1",
        model="test-model",
    )
    response = FakeResponseStream()
    completions = FakeCompletionsAPI(response)
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    deltas = [delta async for delta in client.stream("system", "user")]

    assert deltas == ["第一段", "第二段"]
    assert response.is_closed is True
    assert completions.request is not None
    assert completions.request["stream"] is True
    assert completions.request["temperature"] == 0.1
