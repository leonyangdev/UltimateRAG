"""验证 RAG 上下文的来源编号和长度预算。"""

from ultimate_rag.application import ContextBuilder
from ultimate_rag.domain.models import RetrievalResult


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
