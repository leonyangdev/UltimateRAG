"""验证百炼查询改写与 Rerank 适配器的结构化协议边界。"""

import json
from types import SimpleNamespace

import httpx
import pytest

from ultimate_rag.domain.exceptions import ExternalServiceError
from ultimate_rag.domain.models import RetrievalResult
from ultimate_rag.retrieval import BailianQueryRewriter, BailianReranker


class FakeCompletionsAPI:
    """返回固定 JSON Object，并记录结构化输出参数。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.request: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.request = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


@pytest.mark.asyncio
async def test_query_rewriter_validates_json_and_preserves_one_variant() -> None:
    """改写器应请求 JSON Object，并折叠模型输出中的多余空白。"""

    rewriter = BailianQueryRewriter(
        api_key="test",
        base_url="https://example.test/v1",
        model="qwen-plus",
    )
    fake_api = FakeCompletionsAPI('{"query":"  Milvus   BM25 参数  "}')
    rewriter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=fake_api)
    )

    result = await rewriter.rewrite("BM25 参数")

    assert result == "Milvus BM25 参数"
    assert fake_api.request is not None
    assert fake_api.request["response_format"] == {"type": "json_object"}
    assert fake_api.request["temperature"] == 0.0
    assert fake_api.request["max_tokens"] == 512


@pytest.mark.asyncio
async def test_query_rewriter_rejects_invalid_structured_output() -> None:
    """缺少 query 字段时应抛出可识别外部服务错误，由应用层决定降级。"""

    rewriter = BailianQueryRewriter(
        api_key="test",
        base_url="https://example.test/v1",
        model="qwen-plus",
    )
    rewriter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=FakeCompletionsAPI("{}"))
    )

    with pytest.raises(ExternalServiceError, match="无效响应"):
        await rewriter.rewrite("查询")


@pytest.mark.asyncio
async def test_query_rewriter_rejects_whitespace_only_query() -> None:
    """JSON 字段存在但没有有效检索词时也必须视为畸形模型输出。"""

    rewriter = BailianQueryRewriter(
        api_key="test",
        base_url="https://example.test/v1",
        model="qwen-plus",
    )
    rewriter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=FakeCompletionsAPI('{"query":"   "}'))
    )

    with pytest.raises(ExternalServiceError, match="无效响应"):
        await rewriter.rewrite("查询")


def candidate(chunk_id: str, content: str) -> RetrievalResult:
    """构造 Rerank 请求候选。"""

    return RetrievalResult(
        chunk_id=chunk_id,
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="guide.md",
        content=content,
        heading_path=(),
        score=0.1,
    )


@pytest.mark.asyncio
async def test_reranker_batches_documents_and_maps_response_indexes() -> None:
    """Qwen3 请求应批量携带候选，并按供应商 index 映射回稳定 Chunk ID。"""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.7},
                ]
            },
        )

    reranker = BailianReranker(
        api_key="test",
        url="https://example.test/compatible-api/v1/reranks",
        model="qwen3-rerank",
        transport=httpx.MockTransport(handler),
    )
    results = await reranker.rerank(
        "混合检索",
        [candidate("a", "Dense"), candidate("b", "Sparse")],
        top_n=2,
    )

    assert [result.chunk_id for result in results] == ["b", "a"]
    assert captured["query"] == "混合检索"
    assert captured["documents"] == ["Dense", "Sparse"]
    assert captured["top_n"] == 2
    assert captured["instruct"] == (
        "Given a web search query, retrieve relevant passages that answer the query"
    )


@pytest.mark.asyncio
async def test_reranker_keeps_legacy_gte_protocol_for_explicit_configuration() -> None:
    """旧部署显式配置 GTE 端点时仍应使用原生 input/parameters 协议。"""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"output": {"results": [{"index": 0, "relevance_score": 0.8}]}},
        )

    reranker = BailianReranker(
        api_key="test",
        url="https://example.test/legacy-rerank",
        model="gte-rerank-v2",
        transport=httpx.MockTransport(handler),
    )

    results = await reranker.rerank("query", [candidate("a", "text")], top_n=1)

    assert results[0].chunk_id == "a"
    assert captured["input"] == {"query": "query", "documents": ["text"]}
    assert captured["parameters"] == {"return_documents": False, "top_n": 1}


@pytest.mark.asyncio
async def test_reranker_limits_candidates_by_total_request_token_budget() -> None:
    """请求预算不足时应保留融合排名前缀，并同步收紧 top_n。"""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.9}]},
        )

    reranker = BailianReranker(
        api_key="test",
        url="https://example.test/compatible-api/v1/reranks",
        model="qwen3-rerank",
        # 第一个短候选可进入请求，第二个超长候选会超过带安全余量的总预算。
        max_request_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    results = await reranker.rerank(
        "query",
        [candidate("a", "short"), candidate("b", "word " * 200)],
        top_n=2,
    )

    assert [result.chunk_id for result in results] == ["a"]
    assert captured["documents"] == ["short"]
    assert captured["top_n"] == 1


@pytest.mark.asyncio
async def test_reranker_rejects_out_of_range_index() -> None:
    """供应商异常索引不能错配成另一个 Chunk。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"index": 9, "relevance_score": 1.0}]},
        )

    reranker = BailianReranker(
        api_key="test",
        url="https://example.test/compatible-api/v1/reranks",
        model="qwen3-rerank",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExternalServiceError, match="无效候选索引"):
        await reranker.rerank("query", [candidate("a", "text")], top_n=1)


@pytest.mark.asyncio
async def test_reranker_rejects_non_finite_score() -> None:
    """NaN/Infinity 不能进入排序，否则不同运行时的比较结果将不可预测。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": float("nan")}]}
        )

    reranker = BailianReranker(
        api_key="test",
        url="https://example.test/compatible-api/v1/reranks",
        model="qwen3-rerank",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExternalServiceError, match="无效响应"):
        await reranker.rerank("query", [candidate("a", "text")], top_n=1)
