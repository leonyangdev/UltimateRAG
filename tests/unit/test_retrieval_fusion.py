"""验证 RRF 不混加异构原始分数，并保留每个召回通道的解释信息。"""

import pytest

from ultimate_rag.domain.models import RetrievalResult
from ultimate_rag.retrieval import reciprocal_rank_fusion


def result(chunk_id: str, score: float) -> RetrievalResult:
    """构造只包含融合必需字段的候选。"""

    return RetrievalResult(
        chunk_id=chunk_id,
        knowledge_base_id="kb-1",
        document_id="doc-1",
        filename="guide.md",
        content=chunk_id,
        heading_path=(),
        score=score,
    )


def test_rrf_rewards_chunks_found_by_both_channels() -> None:
    """双通道共同命中的 Chunk 应超过只在单通道排名第一的 Chunk。"""

    fused = reciprocal_rank_fusion(
        [
            ("dense:original", [result("dense-only", 0.99), result("shared", 0.70)]),
            ("sparse:original", [result("shared", 12.0), result("sparse-only", 9.0)]),
        ],
        rank_constant=60,
    )

    assert [item.chunk_id for item in fused] == ["shared", "dense-only", "sparse-only"]
    assert fused[0].dense_score == 0.70
    assert fused[0].sparse_score == 12.0
    assert fused[0].fusion_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].retrieval_sources == ("dense:original", "sparse:original")


def test_rrf_rejects_duplicate_chunk_within_one_ranking() -> None:
    """单个通道重复 ID 会把同一证据重复计分，必须显式拒绝。"""

    duplicate = result("same", 0.8)
    with pytest.raises(ValueError, match="duplicate"):
        reciprocal_rank_fusion([("dense:original", [duplicate, duplicate])])
