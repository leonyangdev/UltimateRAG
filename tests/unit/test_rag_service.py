"""验证 RAG Service 的流式生成仍保持检索证据和引用一致。"""

from collections.abc import AsyncIterator
from typing import cast

import pytest

from ultimate_rag.application import ContextBuilder, RAGService, RetrievalService
from ultimate_rag.domain.models import RetrievalResult
from ultimate_rag.domain.ports import LLMClient


class FakeRetrievalService:
    """返回测试预设的召回结果，不访问 Embedding 服务或 Milvus。"""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results

    async def search(self, knowledge_base_id: str, query: str, top_k: int) -> list[RetrievalResult]:
        """保持测试结果顺序，以便断言 Citation 和答案上下文使用同一排名。"""
        return self._results[:top_k]


class FakeLLMClient:
    """记录流式 Prompt，并用两个确定性增量模拟模型原生输出。"""

    def __init__(self) -> None:
        self.user_prompt: str | None = None

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """实现非流式端口，供类型协议保持完整。"""
        return "完整答案"

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """记录最终 Prompt，并按真实调用顺序产生两个文本片段。"""
        self.user_prompt = user_prompt
        yield "流式"
        yield "答案"


@pytest.mark.asyncio
async def test_stream_answer_returns_text_and_traceable_evidence() -> None:
    """流式答案、Citation 与 RetrievalResult 应引用同一个稳定 Chunk。"""

    result = RetrievalResult(
        chunk_id="chunk-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="guide.md",
        content="UltimateRAG 使用可追溯引用。",
        heading_path=("架构", "引用"),
        score=0.91,
    )
    retrieval = FakeRetrievalService([result])
    llm = FakeLLMClient()
    service = RAGService(
        cast(RetrievalService, retrieval),
        ContextBuilder(max_chars=1000),
        cast(LLMClient, llm),
    )

    stream, citations, results = await service.stream_answer("kb-1", "如何引用？", top_k=5)
    deltas = [delta async for delta in stream]

    assert deltas == ["流式", "答案"]
    assert results == [result]
    assert citations[0].chunk_id == result.chunk_id
    assert llm.user_prompt is not None
    assert "<knowledge_context>" in llm.user_prompt
    assert "UltimateRAG 使用可追溯引用。" in llm.user_prompt


@pytest.mark.asyncio
async def test_stream_answer_skips_llm_when_retrieval_is_empty() -> None:
    """没有证据时应返回固定降级文案，避免模型根据参数知识自由发挥。"""

    llm = FakeLLMClient()
    service = RAGService(
        cast(RetrievalService, FakeRetrievalService([])),
        ContextBuilder(max_chars=1000),
        cast(LLMClient, llm),
    )

    stream, citations, results = await service.stream_answer("kb-1", "未知问题", top_k=5)

    assert [delta async for delta in stream] == ["根据当前知识库无法确定。"]
    assert citations == []
    assert results == []
    assert llm.user_prompt is None
