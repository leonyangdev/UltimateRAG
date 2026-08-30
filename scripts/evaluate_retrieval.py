"""通过公开 V3 API 对人工标注 JSONL 执行离线检索评测。

数据集每行示例：
    ``{"knowledge_base_id":"...","query":"...","relevant_chunk_ids":["...","..."]}``

使用方式：
    ``uv run python scripts/evaluate_retrieval.py dataset.jsonl --mode hybrid --k 5``

脚本只调用 ``/api/retrieval/search``，不会调用答案 LLM。Query Rewrite 与 Rerank 默认沿用服务端
配置，可通过命令行关闭做消融对比。输出是机器可读 JSON，便于在 CI 或表格中比较多次运行。
"""

import argparse
import json
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from ultimate_rag.evaluation import aggregate_metrics, evaluate_ranking


class EvaluationCase(BaseModel):
    """一条最小人工检索标注，不绑定答案生成或模型评分。"""

    knowledge_base_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=4000)
    relevant_chunk_ids: list[str] = Field(min_length=1)
    document_ids: list[str] = Field(default_factory=list, max_length=50)


class RetrievalHit(BaseModel):
    """评估只信任公开响应中的稳定 Chunk ID，忽略展示和模型分数字段。"""

    chunk_id: str = Field(min_length=1, max_length=64)


RETRIEVAL_HITS = TypeAdapter(list[RetrievalHit])


def parse_arguments() -> argparse.Namespace:
    """解析数据集、模式、截断位置和消融开关。"""

    parser = argparse.ArgumentParser(description="Evaluate UltimateRAG V3 retrieval")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--mode", choices=("dense", "sparse", "hybrid"), default="hybrid")
    parser.add_argument("--k", type=int, default=5, choices=range(1, 21))
    parser.add_argument("--candidate-k", type=int, default=30, choices=range(1, 101))
    parser.add_argument("--disable-query-rewrite", action="store_true")
    parser.add_argument("--disable-rerank", action="store_true")
    parser.add_argument(
        "--enable-parent-expansion",
        action="store_true",
        help="include Small2Big database work; it does not change chunk ranking metrics",
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[EvaluationCase]:
    """逐行验证 JSONL，并在错误中保留行号而不是模糊的 JSON 异常。"""

    cases: list[EvaluationCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                cases.append(EvaluationCase.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid evaluation case at line {line_number}") from exc
    if not cases:
        raise ValueError("evaluation dataset is empty")
    return cases


def evaluate(arguments: argparse.Namespace) -> dict[str, object]:
    """逐条请求检索 API，并汇总确定性排名指标。"""

    cases = load_cases(arguments.dataset)
    metrics = []
    with httpx.Client(base_url=arguments.api_url.rstrip("/"), timeout=120.0) as client:
        for case in cases:
            response = client.post(
                "/api/retrieval/search",
                json={
                    "knowledge_base_id": case.knowledge_base_id,
                    "query": case.query,
                    "top_k": arguments.k,
                    "candidate_k": arguments.candidate_k,
                    "mode": arguments.mode,
                    "enable_query_rewrite": not arguments.disable_query_rewrite,
                    "enable_rerank": not arguments.disable_rerank,
                    "enable_parent_expansion": arguments.enable_parent_expansion,
                    "document_ids": case.document_ids,
                },
            )
            response.raise_for_status()
            hits = RETRIEVAL_HITS.validate_python(response.json())
            ranked_ids = [item.chunk_id for item in hits]
            metrics.append(
                evaluate_ranking(
                    ranked_ids,
                    set(case.relevant_chunk_ids),
                    k=arguments.k,
                )
            )

    aggregate = aggregate_metrics(metrics)
    return {
        "query_count": len(cases),
        "mode": arguments.mode,
        "k": arguments.k,
        "candidate_k": arguments.candidate_k,
        "query_rewrite": not arguments.disable_query_rewrite,
        "rerank": not arguments.disable_rerank,
        "parent_expansion": arguments.enable_parent_expansion,
        "precision_at_k": aggregate.precision,
        "recall_at_k": aggregate.recall,
        "mrr_at_k": aggregate.reciprocal_rank,
        "ndcg_at_k": aggregate.ndcg,
    }


def main() -> None:
    """运行评测并以 UTF-8 JSON 输出，不写入仓库或外部状态。"""

    print(json.dumps(evaluate(parse_arguments()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
