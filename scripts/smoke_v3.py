"""通过公开 HTTP API 验证 V3 Dense/Sparse/Hybrid、过滤、重排和 Small2Big。

脚本会创建并最终清理临时知识库，真实调用百炼 Embedding、Query Rewrite 与 Reranker；运行前
必须提供有效 `.env`。它不验证答案生成，避免把检索专项失败与 LLM 文案波动混在一起。
"""

import argparse
import time
from typing import cast
from uuid import uuid4

import httpx

type JsonObject = dict[str, object]


def parse_arguments() -> argparse.Namespace:
    """解析公开 API 地址与后台处理等待上限。"""

    parser = argparse.ArgumentParser(description="Run the UltimateRAG V3 retrieval smoke test")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=240.0)
    return parser.parse_args()


def require_json(response: httpx.Response, operation: str) -> JsonObject:
    """校验 HTTP 成功和 JSON Object 响应，并限制失败正文长度。"""

    if not response.is_success:
        raise RuntimeError(
            f"{operation} failed: HTTP {response.status_code}: {response.text[:800]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{operation} did not return a JSON object")
    return cast(JsonObject, payload)


def wait_until_ready(client: httpx.Client, document_id: str, timeout: float) -> None:
    """轮询持久化文档状态，不把 HTTP 上传成功误当成索引完成。"""

    deadline = time.monotonic() + timeout
    latest: JsonObject = {}
    while time.monotonic() < deadline:
        latest = require_json(client.get(f"/api/documents/{document_id}"), "poll document")
        if latest.get("status") == "READY":
            return
        if latest.get("status") == "FAILED":
            raise RuntimeError(f"background ingestion failed: {latest}")
        time.sleep(1)
    raise TimeoutError(f"document did not reach READY: {latest}")


def upload(client: httpx.Client, knowledge_base_id: str, filename: str, content: str) -> str:
    """断言上传立即返回 202/PENDING，并返回后续轮询使用的稳定 ID。"""

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, content.encode(), "text/markdown")},
    )
    payload = require_json(response, f"upload {filename}")
    if response.status_code != 202 or payload.get("status") != "PENDING":
        raise RuntimeError(f"upload was not accepted asynchronously: {payload}")
    return str(payload["id"])


def explain(client: httpx.Client, **payload: object) -> JsonObject:
    """调用 V3 Explain 端点，让断言同时覆盖结果与阶段 Trace。"""

    return require_json(client.post("/api/retrieval/explain", json=payload), "retrieval explain")


def result_list(payload: JsonObject) -> list[JsonObject]:
    """验证 Explain 的结果数组，避免畸形外部 JSON 造成难理解的类型错误。"""

    values = payload.get("results")
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise RuntimeError(f"retrieval explain returned invalid results: {payload}")
    return cast(list[JsonObject], values)


def run(api_url: str, timeout: float) -> None:
    """执行可回收的 V3 全栈检索验收。"""

    marker = f"ZXQ-{uuid4().hex[:10].upper()}"
    paragraphs = [
        f"第 {index} 段用于构造同一语义章节的相邻 Child，主题是 V3 检索验收与上下文扩展。"
        for index in range(45)
    ]
    paragraphs[22] += f" 精确设备标识为 {marker}，采用 RRF 融合 Dense 与 BM25。"
    target_text = "# 高级检索验收\n\n" + "\n\n".join(paragraphs)
    filter_text = "# 过滤验收\n\nV3 检索验收与上下文扩展。仅属于过滤对照文档。"
    knowledge_base_id: str | None = None
    primary_error: Exception | None = None
    cleanup_error: str | None = None
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=120.0, trust_env=False) as client:
        try:
            require_json(client.get("/api/health"), "health check")
            created = require_json(
                client.post("/api/knowledge-bases", json={"name": f"V3 Smoke {marker}"}),
                "create knowledge base",
            )
            knowledge_base_id = str(created["id"])
            target_id = upload(client, knowledge_base_id, "target.md", target_text)
            filter_id = upload(client, knowledge_base_id, "filter.md", filter_text)
            wait_until_ready(client, target_id, timeout)
            wait_until_ready(client, filter_id, timeout)

            sparse = explain(
                client,
                knowledge_base_id=knowledge_base_id,
                query=marker,
                top_k=3,
                mode="sparse",
                enable_query_rewrite=False,
                enable_rerank=False,
                enable_parent_expansion=False,
                document_ids=[target_id],
            )
            sparse_results = result_list(sparse)
            if not sparse_results or any(
                item.get("document_id") != target_id for item in sparse_results
            ):
                raise RuntimeError(f"sparse exact-term/filter verification failed: {sparse}")

            dense = explain(
                client,
                knowledge_base_id=knowledge_base_id,
                query="如何融合语义检索与关键词检索",
                top_k=3,
                mode="dense",
                enable_query_rewrite=False,
                enable_rerank=False,
                enable_parent_expansion=False,
                document_ids=[target_id],
            )
            dense_results = result_list(dense)
            if not dense_results or any(
                not any(
                    str(source).startswith("dense:")
                    for source in cast(list[object], item.get("retrieval_sources", []))
                )
                for item in dense_results
            ):
                raise RuntimeError(f"dense retrieval verification failed: {dense}")

            hybrid = explain(
                client,
                knowledge_base_id=knowledge_base_id,
                query=f"{marker} 如何融合检索",
                top_k=3,
                mode="hybrid",
                candidate_k=30,
                enable_query_rewrite=True,
                enable_rerank=True,
                enable_parent_expansion=True,
                document_ids=[target_id],
            )
            hybrid_results = result_list(hybrid)
            raw_trace = hybrid.get("trace")
            if not isinstance(raw_trace, dict):
                raise RuntimeError(f"retrieval explain returned invalid trace: {hybrid}")
            trace = cast(JsonObject, raw_trace)
            if not hybrid_results or trace.get("rerank_applied") is not True:
                raise RuntimeError(f"hybrid/rerank verification failed: {hybrid}")
            if trace.get("fallback_reasons") != [] or not any(
                item.get("fusion_score") is not None for item in hybrid_results
            ):
                raise RuntimeError(f"hybrid fusion degraded unexpectedly: {hybrid}")
            if trace.get("parent_expansion_applied") is not True or not any(
                isinstance(item.get("context_chunk_ids"), list)
                and len(cast(list[object], item["context_chunk_ids"])) > 1
                for item in hybrid_results
            ):
                raise RuntimeError(f"Small2Big verification failed: {hybrid}")

            filtered = explain(
                client,
                knowledge_base_id=knowledge_base_id,
                query="V3 检索验收",
                top_k=5,
                mode="sparse",
                enable_query_rewrite=False,
                enable_rerank=False,
                enable_parent_expansion=False,
                document_ids=[filter_id],
            )
            if any(item.get("document_id") != filter_id for item in result_list(filtered)):
                raise RuntimeError(f"document filter leaked another document: {filtered}")
            print("V3 smoke passed: async upload, Sparse, Hybrid/Rerank, Small2Big and filter")
        except Exception as exc:
            primary_error = exc
        finally:
            if knowledge_base_id is not None:
                cleanup = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
                if not cleanup.is_success:
                    cleanup_error = (
                        f"cleanup failed for {knowledge_base_id}: HTTP {cleanup.status_code}: "
                        f"{cleanup.text[:500]}"
                    )
    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(cleanup_error)
        raise primary_error
    if cleanup_error is not None:
        raise RuntimeError(cleanup_error)


if __name__ == "__main__":
    arguments = parse_arguments()
    run(arguments.api_url, arguments.timeout)
