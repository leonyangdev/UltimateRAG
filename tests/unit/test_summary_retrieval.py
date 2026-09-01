"""回归全文总结意图与结构化章节覆盖，防止再次退化为普通 Top-K。"""

from ultimate_rag.application.summary_retrieval import (
    SummaryEvidenceSelector,
    detect_retrieval_intent,
)
from ultimate_rag.domain.models import Chunk, RetrievalIntent, SourceLocator


def _chunk(index: int, heading: str, content: str) -> Chunk:
    return Chunk(
        id=f"chunk-{index}",
        knowledge_base_id="kb-1",
        document_id="doc-1",
        index=index,
        content=content,
        heading_path=(heading,) if heading else (),
        token_count=20 if not heading else 100,
        locator=SourceLocator(heading_path=(heading,) if heading else (), page=index + 1),
        metadata={"filename": "paper.pdf"},
    )


def test_detects_explicit_document_summary_without_routing_local_summary() -> None:
    """全文总结使用章节覆盖，局部章节总结仍应使用相关性检索。"""

    assert detect_retrieval_intent("总结文档的核心内容") is RetrievalIntent.DOCUMENT_SUMMARY
    assert detect_retrieval_intent("Summarize this paper") is RetrievalIntent.DOCUMENT_SUMMARY
    assert detect_retrieval_intent("总结第三节的公式") is RetrievalIntent.FACT


def test_summary_selector_covers_sections_and_excludes_references() -> None:
    """摘要、架构、实验和结论都应进入证据，参考文献不应挤占预算。"""

    chunks = [
        _chunk(-2, "", "版权声明"),
        _chunk(-1, "paper", "论文标题与作者"),
        _chunk(0, "Abstract", "摘要"),
        _chunk(1, "1 Introduction", "引言"),
        _chunk(2, "3 Model Architecture", "架构"),
        _chunk(3, "5 Training", "训练"),
        _chunk(4, "6 Results", "实验结果"),
        _chunk(5, "7 Conclusion", "结论"),
        _chunk(6, "References", "参考文献列表"),
    ]

    results = SummaryEvidenceSelector(max_chunks=6, max_tokens=1024).select(chunks)

    assert [result.content for result in results] == [
        "摘要",
        "引言",
        "架构",
        "训练",
        "实验结果",
        "结论",
    ]
    assert all(result.retrieval_sources == ("structural_coverage",) for result in results)
