"""不依赖向量库的 Reciprocal Rank Fusion（RRF）实现。

模块职责：
    把 Dense、BM25 以及可选改写查询产生的多个有序候选列表融合为一个稳定排名，同时保留
    每个 Chunk 的原始通道分数。

设计背景：
    COSINE 与 BM25 的数值范围和含义不同，直接加权原始分数会引入没有可靠标定依据的权重。
    RRF 只使用名次，避免跨评分体系归一化。原始论文使用 ``k=60``；本实现把它做成显式参数，
    但默认值仍由配置固定为 60，后续只能依据真实评估集调整。

架构边界：
    本模块不发起检索、不调用模型、不读取数据库。纯函数设计使融合行为可以脱离 LLM 和 Milvus
    做确定性单元测试。
"""

from collections.abc import Sequence
from dataclasses import replace

from ultimate_rag.domain.models import RetrievalResult


def reciprocal_rank_fusion(
    rankings: Sequence[tuple[str, Sequence[RetrievalResult]]],
    *,
    rank_constant: int = 60,
) -> list[RetrievalResult]:
    """按 ``sum(1 / (k + rank))`` 融合多个候选列表。

    Args:
        rankings: ``(来源标签, 有序结果)`` 序列。来源标签应以 ``dense`` 或 ``sparse`` 开头，
            例如 ``dense:original``、``sparse:rewrite``。
        rank_constant: 平滑不同排名位置差异的 RRF 常数，必须大于零。

    Returns:
        按融合分数降序排列的去重结果。分数相同时使用 Chunk ID 作为稳定次级顺序，避免
        相同输入在不同进程中因字典或 SDK 返回细节产生随机排名。

    Raises:
        ValueError: ``rank_constant`` 非正，或同一候选列表包含重复 Chunk ID。
    """

    if rank_constant <= 0:
        raise ValueError("rank_constant must be positive")

    merged: dict[str, RetrievalResult] = {}
    fusion_scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}

    for source, results in rankings:
        seen_in_ranking: set[str] = set()
        for rank, result in enumerate(results, start=1):
            if result.chunk_id in seen_in_ranking:
                raise ValueError(f"ranking {source!r} contains duplicate chunk IDs")
            seen_in_ranking.add(result.chunk_id)

            previous = merged.get(result.chunk_id)
            dense_score = previous.dense_score if previous else None
            sparse_score = previous.sparse_score if previous else None
            if source.startswith("dense"):
                dense_score = _maximum(dense_score, result.score)
            elif source.startswith("sparse"):
                sparse_score = _maximum(sparse_score, result.score)

            merged[result.chunk_id] = replace(
                previous or result,
                dense_score=dense_score,
                sparse_score=sparse_score,
            )
            fusion_scores[result.chunk_id] = fusion_scores.get(result.chunk_id, 0.0) + 1.0 / (
                rank_constant + rank
            )
            if source not in sources.setdefault(result.chunk_id, []):
                sources[result.chunk_id].append(source)

    fused = [
        replace(
            result,
            score=fusion_scores[chunk_id],
            fusion_score=fusion_scores[chunk_id],
            retrieval_sources=tuple(sources[chunk_id]),
        )
        for chunk_id, result in merged.items()
    ]
    return sorted(fused, key=lambda item: (-item.score, item.chunk_id))


def _maximum(current: float | None, candidate: float) -> float:
    """保留同一 Chunk 在同类查询变体中的最高原始分数。"""

    return candidate if current is None else max(current, candidate)
