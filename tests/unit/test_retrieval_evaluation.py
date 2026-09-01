"""验证 V3 离线指标的标准截断和边界行为。"""

import pytest

from ultimate_rag.evaluation import aggregate_metrics, evaluate_ranking


def test_evaluate_ranking_computes_precision_recall_mrr_and_ndcg() -> None:
    """相关结果位于第 2、3 名时，四项指标应由同一截断列表计算。"""

    metrics = evaluate_ranking(["x", "a", "b"], {"a", "b"}, k=3)

    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == 1.0
    assert metrics.reciprocal_rank == 0.5
    assert 0.0 < metrics.ndcg < 1.0


def test_aggregate_metrics_uses_query_macro_average() -> None:
    """每条查询权重应相同，不因相关 Chunk 数量不同而改变。"""

    first = evaluate_ranking(["a"], {"a"}, k=1)
    second = evaluate_ranking(["x"], {"b", "c"}, k=1)
    aggregate = aggregate_metrics([first, second])

    assert aggregate.precision == 0.5
    assert aggregate.recall == 0.5
    assert aggregate.reciprocal_rank == 0.5
    assert aggregate.ndcg == 0.5


def test_evaluate_ranking_rejects_empty_labels_and_duplicates() -> None:
    """无标注和重复排名都会让指标失真，应在本地立即失败。"""

    with pytest.raises(ValueError, match="cannot be empty"):
        evaluate_ranking(["a"], set(), k=1)
    with pytest.raises(ValueError, match="unique"):
        evaluate_ranking(["a", "a"], {"a"}, k=2)
