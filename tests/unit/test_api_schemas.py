"""验证 V3 检索 API 的边界清洗和领域配置映射。"""

import pytest
from api.schemas import ChatRequest, RetrievalRequest, RetrievalResultResponse
from pydantic import ValidationError

from ultimate_rag.domain.models import (
    BlockType,
    DocumentAsset,
    RetrievalMode,
    RetrievalOptions,
    RetrievalResult,
    SourceLocator,
)


def test_retrieval_request_normalizes_and_deduplicates_document_filter() -> None:
    """首尾空白不应进入 Milvus 表达式，重复 ID 不应放大过滤条件。"""

    request = RetrievalRequest(
        knowledge_base_id="  kb-1 ",
        query="  BM25 是什么？  ",
        document_ids=[" doc-1 ", "doc-1", "doc-2"],
    )
    options = request.to_options(
        RetrievalOptions(
            mode=RetrievalMode.DENSE,
            candidate_k=12,
            enable_query_rewrite=False,
            enable_rerank=False,
            enable_parent_expansion=False,
        )
    )

    assert request.knowledge_base_id == "kb-1"
    assert request.query == "BM25 是什么？"
    assert options.document_ids == ("doc-1", "doc-2")
    assert options.mode is RetrievalMode.DENSE
    assert options.candidate_k == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [("knowledge_base_id", "  "), ("query", "\t\n"), ("document_ids", [" "])],
)
def test_retrieval_request_rejects_blank_values(field: str, value: object) -> None:
    """空白输入必须在 Pydantic HTTP 边界变成 422，而不是应用层 ValueError/500。"""

    payload: dict[str, object] = {
        "knowledge_base_id": "kb-1",
        "query": "query",
        "document_ids": [],
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate(payload)


def test_chat_request_keeps_question_alias_and_inherited_validation() -> None:
    """Chat 继续接受公开的 question 字段，并继承查询空白清洗。"""

    request = ChatRequest(knowledge_base_id="kb-1", question="  查询内容  ")

    assert request.query == "查询内容"


def test_pdf_retrieval_result_exposes_visual_evidence_metadata() -> None:
    """带页码的 TABLE 命中应向前端提供类型与受控 Chunk 预览地址。"""

    response = RetrievalResultResponse.from_domain(
        RetrievalResult(
            chunk_id="chunk-1",
            knowledge_base_id="kb-1",
            document_id="doc-1",
            filename="paper.pdf",
            content="| Model | BLEU |",
            heading_path=("Results",),
            score=0.9,
            locator=SourceLocator(page=8, bbox=(10.0, 20.0, 200.0, 120.0)),
            content_types=(BlockType.TABLE,),
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
                    locator=SourceLocator(page=8),
                ),
            ),
        )
    )

    assert response.content_types == ["TABLE"]
    assert response.preview_url == "/api/chunks/chunk-1/preview"
    assert response.assets[0].content_url == "/api/assets/asset-1/content"
    assert response.assets[0].model_dump().get("object_key") is None
