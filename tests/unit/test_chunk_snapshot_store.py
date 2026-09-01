"""验证本地 Chunk JSON 快照的结构、幂等覆盖、原子性和路径安全。"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ultimate_rag.infrastructure.storage.chunk_snapshot as snapshot_module
from ultimate_rag.domain.models import (
    Chunk,
    Document,
    DocumentStatus,
    ParsedDocument,
    SourceLocator,
)
from ultimate_rag.infrastructure.storage import LocalChunkSnapshotStore


@pytest.fixture(autouse=True)
def _run_snapshot_file_operations_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """单测只验证文件语义，不重复测试标准库线程调度。

    生产实现仍使用 ``asyncio.to_thread``，避免 Worker 心跳被文件系统阻塞；测试内改为同步
    执行可让 Windows 临时目录断言稳定且快速，同时保留完全相同的写入和删除代码路径。
    """

    async def run_inline(
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        return function(*args, **kwargs)

    monkeypatch.setattr(snapshot_module.asyncio, "to_thread", run_inline)


def _document() -> Document:
    """构造带完整来源事实的处理中 PDF 文档。"""

    now = datetime.now(UTC)
    return Document(
        id="doc-1",
        knowledge_base_id="kb-1",
        filename="注意力机制论文.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        object_key="kb-1/doc-1/source.pdf",
        sha256="source-sha256",
        status=DocumentStatus.CHUNKING,
        parser_name="pdf-docling",
        parser_version="3.0",
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _chunk(content: str = "编码器通过自注意力读取输入。") -> Chunk:
    """构造含图片、Parent-Child 和精确 PDF Locator 的最终 Chunk。"""

    locator = SourceLocator(
        heading_path=("Model Architecture", "Encoder"),
        page=3,
        bbox=(10.5, 20.0, 300.0, 420.25),
    )
    return Chunk(
        id="chunk-1",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        index=0,
        content=content,
        heading_path=locator.heading_path,
        token_count=18,
        locator=locator,
        metadata={
            "block_types": ["TEXT", "IMAGE"],
            "asset_ids": ["asset-1"],
            "split_strategy": "single",
            "tokenizer": "cl100k_base",
            "parent_id": "parent-1",
            "parent_child_index": 0,
            "parent_child_count": 1,
            "filename": "注意力机制论文.pdf",
            "source_locator": locator.to_metadata(),
        },
    )


def _parsed_document() -> ParsedDocument:
    """构造 Parser 顶层 metadata；Block/Asset 不应被重复写入快照。"""

    return ParsedDocument(
        document_id="doc-1",
        blocks=(),
        metadata={"parser": "pdf-docling", "layout_engine": "docling"},
    )


@pytest.mark.asyncio
async def test_store_writes_complete_utf8_snapshot_without_embedding(tmp_path: Path) -> None:
    """快照应原样保存最终 Chunk、Locator 和 metadata，但不能包含向量。"""

    store = LocalChunkSnapshotStore(tmp_path)
    await store.save(
        document=_document(),
        parsed_document=_parsed_document(),
        parser_name="pdf-docling",
        parser_version="3.0",
        chunks=[_chunk()],
    )

    snapshot_path = tmp_path / "kb-1" / "doc-1" / "chunks.json"
    raw = snapshot_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    # ensure_ascii=False 让中文正文和文件名可以直接人工检查，不需要先反解 \u 转义。
    assert "编码器通过自注意力读取输入。" in raw
    assert "注意力机制论文.pdf" in raw
    assert payload["schema_version"] == 1
    assert payload["snapshot_stage"] == "post_chunk_pre_embedding"
    assert payload["document"]["knowledge_base_id"] == "kb-1"
    assert payload["document"]["parser_version"] == "3.0"
    assert payload["parsed_metadata"]["layout_engine"] == "docling"
    assert payload["chunk_count"] == 1
    assert payload["chunks"][0]["locator"]["page"] == 3
    assert payload["chunks"][0]["metadata"]["asset_ids"] == ["asset-1"]
    assert "embedding" not in payload["chunks"][0]


@pytest.mark.asyncio
async def test_store_replaces_same_document_snapshot_instead_of_appending(tmp_path: Path) -> None:
    """重新解析应稳定覆盖同一路径，不生成重复或无界历史文件。"""

    store = LocalChunkSnapshotStore(tmp_path)
    common = {
        "document": _document(),
        "parsed_document": _parsed_document(),
        "parser_name": "pdf-docling",
        "parser_version": "3.0",
    }
    await store.save(**common, chunks=[_chunk("旧版切块")])
    await store.save(**common, chunks=[_chunk("新版切块")])

    document_directory = tmp_path / "kb-1" / "doc-1"
    files = list(document_directory.iterdir())
    payload = json.loads((document_directory / "chunks.json").read_text(encoding="utf-8"))

    assert files == [document_directory / "chunks.json"]
    assert payload["chunks"][0]["content"] == "新版切块"


@pytest.mark.asyncio
async def test_store_keeps_previous_snapshot_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发布失败时旧 JSON 必须保持完整，临时文件也不能泄漏。"""

    store = LocalChunkSnapshotStore(tmp_path)
    common = {
        "document": _document(),
        "parsed_document": _parsed_document(),
        "parser_name": "pdf-docling",
        "parser_version": "3.0",
    }
    await store.save(**common, chunks=[_chunk("旧版完整快照")])
    snapshot_path = tmp_path / "kb-1" / "doc-1" / "chunks.json"
    previous_bytes = snapshot_path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(snapshot_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        await store.save(**common, chunks=[_chunk("不应发布的新快照")])

    assert snapshot_path.read_bytes() == previous_bytes
    assert list(snapshot_path.parent.iterdir()) == [snapshot_path]


@pytest.mark.asyncio
async def test_store_rejects_path_traversal_ids(tmp_path: Path) -> None:
    """即使上游事实异常，系统 ID 也不能逃逸配置的快照根目录。"""

    store = LocalChunkSnapshotStore(tmp_path)
    unsafe_document = replace(_document(), id="../outside")

    with pytest.raises(ValueError, match="document_id"):
        await store.save(
            document=unsafe_document,
            parsed_document=_parsed_document(),
            parser_name="pdf-docling",
            parser_version="3.0",
            chunks=[_chunk()],
        )

    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.asyncio
async def test_store_deletes_document_snapshot_and_empty_parent(tmp_path: Path) -> None:
    """文档删除必须同时清理包含业务明文的快照与空知识库目录。"""

    store = LocalChunkSnapshotStore(tmp_path)
    await store.save(
        document=_document(),
        parsed_document=_parsed_document(),
        parser_name="pdf-docling",
        parser_version="3.0",
        chunks=[_chunk()],
    )

    await store.delete_by_document("kb-1", "doc-1")

    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []
