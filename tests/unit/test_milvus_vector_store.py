"""验证 Milvus 写入和删除完成后才向应用层报告成功。"""

from typing import Any

from ultimate_rag.domain.models import Chunk, SourceLocator
from ultimate_rag.vectorstores import MilvusVectorStore


class FakeMilvusClient:
    """记录 Milvus 调用顺序，用于验证持久化边界而不连接真实服务。"""

    def __init__(self) -> None:
        """初始化空调用记录。"""

        self.calls: list[tuple[str, str]] = []

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> None:
        """记录写入调用以及本批数据行数。"""

        self.calls.append(("upsert", f"{collection_name}:{len(data)}"))

    def delete(self, *, collection_name: str, filter: str) -> None:
        """记录按业务范围删除的过滤表达式。"""

        self.calls.append(("delete", f"{collection_name}:{filter}"))

    def flush(self, *, collection_name: str) -> None:
        """记录持久化调用，供测试断言其发生在变更之后。"""

        self.calls.append(("flush", collection_name))


def store_with_fake_client() -> tuple[MilvusVectorStore, FakeMilvusClient]:
    """构造跳过真实网络初始化的向量存储与假客户端。"""

    client = FakeMilvusClient()
    store = object.__new__(MilvusVectorStore)
    store._client = client  # type: ignore[assignment]
    store._collection = "knowledge_chunks"
    store._sparse_collection = "knowledge_chunks_sparse_v3"
    store._dimension = 2
    store._bm25_k1 = 1.2
    store._bm25_b = 0.75
    return store, client


def test_upsert_flushes_after_data_change() -> None:
    """向量写入必须在 flush 完成后才允许应用层继续推进状态。"""

    store, client = store_with_fake_client()

    store._upsert_sync([{"id": "chunk-1"}])

    assert client.calls == [
        ("upsert", "knowledge_chunks:1"),
        ("flush", "knowledge_chunks"),
    ]


def test_delete_flushes_after_data_change() -> None:
    """删除操作必须持久化，避免 204 后旧向量在重启时重新出现。"""

    store, client = store_with_fake_client()

    store._delete_sync('document_id == "doc-1"')

    assert client.calls == [
        ("delete", 'knowledge_chunks:document_id == "doc-1"'),
        ("flush", "knowledge_chunks"),
    ]


def test_delete_both_cleans_dense_and_sparse_indexes() -> None:
    """业务删除只有在两个派生集合都 Flush 后才能成功。"""

    store, client = store_with_fake_client()

    store._delete_both_sync('document_id == "doc-1"')

    assert client.calls == [
        ("delete", 'knowledge_chunks:document_id == "doc-1"'),
        ("flush", "knowledge_chunks"),
        ("delete", 'knowledge_chunks_sparse_v3:document_id == "doc-1"'),
        ("flush", "knowledge_chunks_sparse_v3"),
    ]


def test_retrieval_result_reads_v2_locator_and_v1_heading_path() -> None:
    """同一 JSON 字段必须兼容 V2 Locator 字典与升级前的 V1 标题数组。"""

    common = {
        "chunk_id": "chunk-1",
        "knowledge_base_id": "kb-1",
        "document_id": "doc-1",
        "filename": "source.pdf",
        "content": "content",
    }
    v2 = MilvusVectorStore._retrieval_result(
        {"distance": 0.9, "entity": {**common, "heading_path": {"page": 3}}}
    )
    v1 = MilvusVectorStore._retrieval_result(
        {"distance": 0.8, "entity": {**common, "heading_path": ["RAG", "Index"]}}
    )

    assert v2.locator is not None and v2.locator.page == 3
    assert v1.heading_path == ("RAG", "Index")
    assert v1.locator is not None and v1.locator.heading_path == ("RAG", "Index")
    assert v2.dense_score == 0.9
    assert v2.retrieval_sources == ("dense",)


def test_sparse_row_and_filter_preserve_rebuild_metadata() -> None:
    """历史 Chunk 回填应包含原文定位，文档过滤值必须安全转义。"""

    chunk = Chunk(
        id="chunk-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        index=0,
        content="Milvus BM25",
        heading_path=("检索",),
        token_count=3,
        locator=SourceLocator(heading_path=("检索",), page=2),
        metadata={"filename": "guide.pdf"},
    )

    row = MilvusVectorStore._sparse_row(chunk)
    expression = MilvusVectorStore._filter_expression(
        'kb-1" or id != "',
        ['doc-1" or id != "'],
    )

    assert row["content"] == "Milvus BM25"
    assert row["heading_path"] == {"heading_path": ["检索"], "page": 2}
    assert expression == (
        'knowledge_base_id == "kb-1\\" or id != \\"" and document_id in ["doc-1\\" or id != \\""]'
    )
