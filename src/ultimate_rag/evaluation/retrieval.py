"""可重复、与模型供应商无关的检索排名指标。

模块职责：
    根据人工标注的相关 Chunk ID 计算 Precision@k、Recall@k、MRR@k 和二元相关性 nDCG@k，
    并聚合多条查询的宏平均值。

设计背景：
    V3 的候选宽度、RRF 常数、Reranker 和 Query Rewrite 不能凭主观“感觉更准”调参。这里提供
    最小离线验证基础，让同一 JSONL 查询集可以比较 Dense、Sparse、Hybrid 和消融配置。

架构边界：
    本模块只处理稳定 Chunk ID，不调用检索 API、不读取 PostgreSQL，也不记录线上 Trace、成本
    或用户反馈；完整 Golden Dataset 管理与回归平台仍属于 V5 RAGOps。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """一条查询或一个查询集在同一 ``k`` 下的四项检索指标。"""

    precision: float
    recall: float
    reciprocal_rank: float
    ndcg: float


def evaluate_ranking(
    ranked_chunk_ids: Sequence[str],
    relevant_chunk_ids: set[str],
    *,
    k: int,
) -> RetrievalMetrics:
    """计算一条人工标注查询的排名质量。

    Args:
        ranked_chunk_ids: 按最终检索顺序排列的唯一 Chunk ID。
        relevant_chunk_ids: 至少一个人工确认相关的 Chunk ID。
        k: 评估截断位置。Precision 分母固定为 ``k``，因此少返回结果会被明确惩罚。

    Raises:
        ValueError: ``k`` 非正、标注集合为空或排名含重复 ID。
    """

    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_chunk_ids:
        raise ValueError("relevant_chunk_ids cannot be empty")
    if len(set(ranked_chunk_ids)) != len(ranked_chunk_ids):
        raise ValueError("ranked_chunk_ids must be unique")

    top_ids = list(ranked_chunk_ids[:k])
    relevance = [1 if chunk_id in relevant_chunk_ids else 0 for chunk_id in top_ids]
    hit_count = sum(relevance)
    reciprocal_rank = next(
        (1.0 / rank for rank, is_relevant in enumerate(relevance, start=1) if is_relevant),
        0.0,
    )
    dcg = sum(
        is_relevant / math.log2(rank + 1)
        for rank, is_relevant in enumerate(relevance, start=1)
    )
    ideal_hits = min(len(relevant_chunk_ids), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return RetrievalMetrics(
        precision=hit_count / k,
        recall=hit_count / len(relevant_chunk_ids),
        reciprocal_rank=reciprocal_rank,
        ndcg=dcg / ideal_dcg,
    )


def aggregate_metrics(values: Sequence[RetrievalMetrics]) -> RetrievalMetrics:
    """对查询逐条指标做宏平均，避免长文档或多标注查询支配总体结果。"""

    if not values:
        raise ValueError("metrics cannot be empty")
    count = len(values)
    return RetrievalMetrics(
        precision=sum(value.precision for value in values) / count,
        recall=sum(value.recall for value in values) / count,
        reciprocal_rank=sum(value.reciprocal_rank for value in values) / count,
        ndcg=sum(value.ndcg for value in values) / count,
    )
