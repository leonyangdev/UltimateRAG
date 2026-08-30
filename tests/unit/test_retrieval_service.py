"""验证 V3 高级检索编排、降级、取消、过滤和 Small2Big 上下文扩展。"""

import asyncio
from collections.abc import Sequence
from typing import cast

import pytest

from ultimate_rag.application import RetrievalService
from ultimate_rag.domain.models import (
    Chunk,
    EmbeddedChunk,
    RerankResult,
    RetrievalMode,
    RetrievalOptions,
    RetrievalResult,
    SourceLocator,
)
from ultimate_rag.domain.ports import Embedder, QueryRewriter, Reranker, VectorStore
from ultimate_rag.infrastructure.database.repository import Repository


def retrieval_result(chunk_id: str, score: float, content: str | None = None) -> RetrievalResult:
    """构造同一 READY 文档内的测试候选。"""

    return RetrievalResult(
        chunk_id=chunk_id,
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="guide.md",
        content=content or chunk_id,
        heading_path=("V3",),
        score=score,
        locator=SourceLocator(heading_path=("V3",), page=1),
    )


class FakeEmbedder:
    """按查询文本返回可辨认向量，并记录是否发生外部模型调用。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index)] for index, _text in enumerate(texts)]

    async def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [1.0 if query == "原始查询" else 2.0]


class FakeVectorStore:
    """返回预设 Dense/Sparse 排名，可选择让 Sparse 通道失败。"""

    def __init__(self, *, fail_sparse: bool = False, cancel_sparse: bool = False) -> None:
        self.fail_sparse = fail_sparse
        self.cancel_sparse = cancel_sparse
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def ensure_collection(self) -> None:
        return None

    async def upsert(self, chunks: Sequence[EmbeddedChunk]) -> None:
        return None

    async def upsert_sparse(self, chunks: Sequence[Chunk]) -> None:
        return None

    async def search(
        self,
        query_vector: Sequence[float],
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
    ) -> list[RetrievalResult]:
        variant = "original" if query_vector[0] == 1.0 else "rewrite"
        self.calls.append(("dense", variant, tuple(document_ids)))
        values = (
            [retrieval_result("a", 0.9), retrieval_result("b", 0.8)]
            if variant == "original"
            else [retrieval_result("c", 0.95), retrieval_result("a", 0.7)]
        )
        return values[:top_k]

    async def search_sparse(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        document_ids: Sequence[str] = (),
    ) -> list[RetrievalResult]:
        variant = "original" if query == "原始查询" else "rewrite"
        self.calls.append(("sparse", variant, tuple(document_ids)))
        if self.cancel_sparse:
            raise asyncio.CancelledError
        if self.fail_sparse:
            raise RuntimeError("sparse unavailable")
        values = (
            [retrieval_result("b", 12.0), retrieval_result("c", 8.0)]
            if variant == "original"
            else [retrieval_result("a", 10.0), retrieval_result("c", 7.0)]
        )
        return values[:top_k]

    async def delete_by_document(self, document_id: str) -> None:
        return None

    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None:
        return None


class FakeRepository:
    """提供 READY 文档交集和可选的相邻 Chunk。"""

    def __init__(
        self,
        *,
        ready_ids: set[str] | None = None,
        contexts: dict[str, list[Chunk]] | None = None,
    ) -> None:
        self.ready_ids = {"doc-1"} if ready_ids is None else ready_ids
        self.contexts = contexts or {}

    async def list_ready_document_ids(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str] = (),
    ) -> set[str]:
        return self.ready_ids.intersection(document_ids) if document_ids else self.ready_ids

    async def get_chunks_with_neighbors(
        self,
        chunk_ids: Sequence[str],
        *,
        window: int,
    ) -> dict[str, list[Chunk]]:
        return {chunk_id: self.contexts.get(chunk_id, []) for chunk_id in chunk_ids}


class FakeQueryRewriter:
    """返回一个固定检索变体。"""

    async def rewrite(self, query: str) -> str | None:
        return "改写查询"


class SameQueryRewriter:
    """模拟返回仅首尾空白不同的无效重复改写。"""

    async def rewrite(self, query: str) -> str | None:
        return f"  {query}  "


class FakeReranker:
    """把 C、B 排在前两名，以证明第二阶段确实改变 RRF 顺序。"""

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        top_n: int,
    ) -> list[RerankResult]:
        available = {candidate.chunk_id for candidate in candidates}
        return [
            RerankResult(chunk_id=chunk_id, score=score)
            for chunk_id, score in (("c", 0.97), ("b", 0.83))
            if chunk_id in available
        ][:top_n]


@pytest.mark.asyncio
async def test_hybrid_retrieval_rewrites_fuses_reranks_and_filters() -> None:
    """四个召回列表应融合去重、下推文档过滤，再按 Reranker 输出最终顺序。"""

    embedder = FakeEmbedder()
    vector_store = FakeVectorStore()
    service = RetrievalService(
        cast(Embedder, embedder),
        cast(VectorStore, vector_store),
        cast(Repository, FakeRepository()),
        query_rewriter=cast(QueryRewriter, FakeQueryRewriter()),
        reranker=cast(Reranker, FakeReranker()),
    )

    run = await service.retrieve(
        "kb-1",
        "原始查询",
        2,
        RetrievalOptions(
            mode=RetrievalMode.HYBRID,
            candidate_k=10,
            enable_query_rewrite=True,
            enable_rerank=True,
            enable_parent_expansion=False,
            document_ids=("doc-1",),
        ),
    )

    assert [result.chunk_id for result in run.results] == ["c", "b"]
    assert all(result.rerank_score is not None for result in run.results)
    assert run.trace.query_variants == ("原始查询", "改写查询")
    assert run.trace.candidate_count == 3
    assert run.trace.rewrite_applied is True
    assert run.trace.rerank_applied is True
    assert len(vector_store.calls) == 4
    assert all(call[2] == ("doc-1",) for call in vector_store.calls)


@pytest.mark.asyncio
async def test_duplicate_rewrite_is_not_recalled_twice() -> None:
    """Service 不应只信任 Adapter；等价改写不能重复产生 Dense/Sparse 模型调用。"""

    vector_store = FakeVectorStore()
    service = RetrievalService(
        cast(Embedder, FakeEmbedder()),
        cast(VectorStore, vector_store),
        cast(Repository, FakeRepository()),
        query_rewriter=cast(QueryRewriter, SameQueryRewriter()),
    )

    run = await service.retrieve(
        "kb-1",
        "原始查询",
        2,
        RetrievalOptions(
            mode=RetrievalMode.HYBRID,
            enable_query_rewrite=True,
            enable_rerank=False,
            enable_parent_expansion=False,
        ),
    )

    assert run.trace.query_variants == ("原始查询",)
    assert run.trace.rewrite_applied is False
    assert len(vector_store.calls) == 2


@pytest.mark.asyncio
async def test_empty_document_filter_skips_embedding_and_vector_search() -> None:
    """过滤条件与 READY 事实没有交集时，不应产生模型费用或访问 Milvus。"""

    embedder = FakeEmbedder()
    vector_store = FakeVectorStore()
    service = RetrievalService(
        cast(Embedder, embedder),
        cast(VectorStore, vector_store),
        cast(Repository, FakeRepository(ready_ids={"another-doc"})),
    )

    run = await service.retrieve(
        "kb-1",
        "原始查询",
        5,
        RetrievalOptions(document_ids=("doc-1",)),
    )

    assert run.results == ()
    assert embedder.queries == []
    assert vector_store.calls == []


@pytest.mark.asyncio
async def test_hybrid_degrades_to_dense_when_sparse_channel_fails() -> None:
    """Hybrid 的 Sparse 临时故障应明确降级，保留可用 Dense 结果。"""

    service = RetrievalService(
        cast(Embedder, FakeEmbedder()),
        cast(VectorStore, FakeVectorStore(fail_sparse=True)),
        cast(Repository, FakeRepository()),
    )
    run = await service.retrieve(
        "kb-1",
        "原始查询",
        2,
        RetrievalOptions(
            mode=RetrievalMode.HYBRID,
            enable_query_rewrite=False,
            enable_rerank=False,
            enable_parent_expansion=False,
        ),
    )

    assert [result.chunk_id for result in run.results] == ["a", "b"]
    assert "sparse_retrieval_failed" in run.trace.fallback_reasons


@pytest.mark.asyncio
async def test_request_cancellation_is_not_treated_as_channel_fallback() -> None:
    """客户端或进程取消检索时必须立即传播，不能继续生成一个看似成功的降级结果。"""

    service = RetrievalService(
        cast(Embedder, FakeEmbedder()),
        cast(VectorStore, FakeVectorStore(cancel_sparse=True)),
        cast(Repository, FakeRepository()),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.retrieve(
            "kb-1",
            "原始查询",
            2,
            RetrievalOptions(
                mode=RetrievalMode.HYBRID,
                enable_query_rewrite=False,
                enable_rerank=False,
                enable_parent_expansion=False,
            ),
        )


@pytest.mark.asyncio
async def test_parent_expansion_keeps_same_parent_and_token_budget() -> None:
    """Small2Big 只带回同 Parent 相邻 Child，并保留原始命中文本用于解释。"""

    def chunk(index: int, parent_id: str, content: str, tokens: int = 100) -> Chunk:
        return Chunk(
            id=f"child-{index}",
            knowledge_base_id="kb-1",
            document_id="doc-1",
            index=index,
            content=content,
            heading_path=("V3",),
            token_count=tokens,
            locator=SourceLocator(heading_path=("V3",), page=1),
            metadata={"parent_id": parent_id},
        )

    before = chunk(0, "parent-1", "前文")
    matched = chunk(1, "parent-1", "命中")
    other_parent = chunk(2, "parent-2", "下一节")
    repository = FakeRepository(contexts={"a": [before, matched, other_parent]})
    vector_store = FakeVectorStore()
    # Dense Fake 默认命中 a；让 PostgreSQL 中的稳定 Chunk ID 与该 Hit 对齐。
    matched = replace_chunk_id(matched, "a")
    repository.contexts = {"a": [before, matched, other_parent]}
    service = RetrievalService(
        cast(Embedder, FakeEmbedder()),
        cast(VectorStore, vector_store),
        cast(Repository, repository),
        parent_window=1,
        parent_max_tokens=250,
    )

    run = await service.retrieve(
        "kb-1",
        "原始查询",
        1,
        RetrievalOptions(
            mode=RetrievalMode.DENSE,
            enable_query_rewrite=False,
            enable_rerank=False,
            enable_parent_expansion=True,
        ),
    )

    assert run.results[0].content == "前文\n\n命中"
    assert run.results[0].matched_content == "a"
    assert run.results[0].context_chunk_ids == ("child-0", "a")
    assert "下一节" not in run.results[0].content
    assert run.trace.parent_expansion_applied is True


def replace_chunk_id(chunk: Chunk, chunk_id: str) -> Chunk:
    """为 Parent 测试把数据库 Chunk ID 与向量命中 ID 对齐。"""

    return Chunk(
        id=chunk_id,
        knowledge_base_id=chunk.knowledge_base_id,
        document_id=chunk.document_id,
        index=chunk.index,
        content=chunk.content,
        heading_path=chunk.heading_path,
        token_count=chunk.token_count,
        locator=chunk.locator,
        metadata=chunk.metadata,
    )


def test_retrieval_options_rejects_invalid_runtime_mode() -> None:
    """直接 Python 调用也不能绕过枚举边界后静默得到空任务列表。"""

    with pytest.raises(ValueError, match="RetrievalMode"):
        RetrievalOptions(mode=cast(RetrievalMode, "lexical"))
