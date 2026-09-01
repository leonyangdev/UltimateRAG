"""验证 RAG 上下文的来源编号和长度预算。"""

from ultimate_rag.application import ContextBuilder
from ultimate_rag.domain.models import BlockType, DocumentAsset, RetrievalResult, SourceLocator


def result(index: int, content: str) -> RetrievalResult:
    """构造具备完整可追溯字段的检索结果测试数据。"""

    return RetrievalResult(
        chunk_id=f"chunk-{index}",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="rag.md",
        content=content,
        heading_path=("检索", "Dense"),
        score=0.9,
    )


def test_context_builder_labels_sources_and_honors_budget() -> None:
    """上下文应标注来源并严格限制在配置的字符预算内。"""

    context = ContextBuilder(max_chars=180).build(
        [result(1, "第一段知识" * 10), result(2, "第二段知识" * 10)]
    )

    assert context.startswith("[来源 1]")
    assert "rag.md" in context
    assert len(context) <= 180


def test_context_builder_handles_empty_results() -> None:
    """无检索结果时返回空上下文，避免制造不存在的引用。"""

    assert ContextBuilder().build([]) == ""


def test_context_builder_declares_controlled_image_resource_before_content() -> None:
    """图片证据必须把精确 asset:// Markdown 交给模型，并位于可能被截断的正文之前。"""

    value = result(1, "架构图的视觉描述")
    value = RetrievalResult(
        **{
            field: getattr(value, field)
            for field in (
                "chunk_id",
                "knowledge_base_id",
                "document_id",
                "filename",
                "content",
                "heading_path",
                "score",
            )
        },
        assets=(
            DocumentAsset(
                id="asset-1",
                document_id="doc-1",
                block_id="block-1",
                kind=BlockType.IMAGE,
                object_key="kb/doc/assets/asset-1.jpg",
                media_type="image/jpeg",
                filename="asset-1.jpg",
                title="Transformer 架构图",
                description="Encoder 与 Decoder",
                sha256="abc",
                locator=SourceLocator(page=3),
            ),
        ),
    )

    context = ContextBuilder(max_chars=1000).build([value])

    marker = "![Transformer 架构图](asset://asset-1)"
    assert marker in context
    assert context.index(marker) < context.index("内容：")
