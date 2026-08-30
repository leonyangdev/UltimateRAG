"""对已启动的 UltimateRAG 执行一次可回收的 V1 真实闭环验收。

脚本职责：
    通过公开 HTTP API 创建临时知识库、上传固定 Markdown、验证 READY、独立检索并消费
    AI SDK 流式问答，最后删除临时知识库。它不会绕过 API 直接访问数据库或外部服务。

设计背景：
    单元测试只能证明各 Adapter 和 Service 的局部行为。本脚本用于发布前证明 PostgreSQL、
    MinIO、Milvus、百炼 Embedding、百炼 LLM、FastAPI 流式协议确实可以共同工作。

使用方式：
    ``uv run python scripts/smoke_v1.py --api-url http://localhost:8000``

注意事项：
    执行会调用真实百炼服务并产生少量模型用量。无论验收成功还是中途失败，脚本都会尽力
    删除本次创建的知识库；清理失败会明确输出临时知识库 ID，便于人工补偿。
"""

import argparse
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def parse_arguments() -> argparse.Namespace:
    """解析 API 地址、Fixture 和验证问题，保留可重复运行所需的最小参数。"""

    parser = argparse.ArgumentParser(description="Run the UltimateRAG V1 smoke test")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/rag.md"))
    parser.add_argument("--question", default="BGE-M3 是什么？")
    return parser.parse_args()


def require_success(response: httpx.Response, operation: str) -> None:
    """把非成功响应转换为包含业务操作和有限响应正文的验收错误。"""

    if response.is_success:
        return
    # 响应正文可能来自反向代理而非 JSON；截断可以避免异常页无界刷屏。
    body = response.text[:1000]
    raise RuntimeError(f"{operation} failed: HTTP {response.status_code}: {body}")


def ui_stream_events(response: httpx.Response) -> Iterator[dict[str, Any]]:
    """从 AI SDK SSE 响应中解析 JSON Event，并忽略空行与终止哨兵。"""

    for line in response.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            return
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise RuntimeError("stream event must be a JSON object")
        yield event


def wait_until_ready(
    client: httpx.Client,
    document_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """轮询异步文档详情，直到 READY、FAILED 或超时。"""

    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/documents/{document_id}")
        require_success(response, "poll document")
        latest = response.json()
        if latest.get("status") == "READY":
            return latest
        if latest.get("status") == "FAILED":
            raise RuntimeError(f"background ingestion failed: {latest}")
        time.sleep(1)
    raise TimeoutError(f"document did not reach READY: {latest}")


def run_smoke_test(api_url: str, fixture: Path, question: str) -> None:
    """执行 Create → Upload → Retrieve → Stream Chat → Delete 完整验收。"""

    if not fixture.is_file():
        raise FileNotFoundError(f"Smoke fixture not found: {fixture}")

    knowledge_base_id: str | None = None
    primary_error: Exception | None = None
    cleanup_error: str | None = None
    # 本地验收不应继承开发机代理，否则 localhost 请求可能被错误发送到外部代理。
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=120.0, trust_env=False) as client:
        try:
            health = client.get("/api/health")
            require_success(health, "health check")

            create = client.post(
                "/api/knowledge-bases",
                json={
                    "name": f"V1 Smoke {uuid4().hex[:8]}",
                    "description": "由 scripts/smoke_v1.py 创建，可安全清理。",
                },
            )
            require_success(create, "create knowledge base")
            knowledge_base_id = str(create.json()["id"])
            print(f"[1/5] Knowledge base created: {knowledge_base_id}")

            with fixture.open("rb") as file_handle:
                upload = client.post(
                    f"/api/knowledge-bases/{knowledge_base_id}/documents",
                    files={"file": (fixture.name, file_handle, "text/markdown")},
                )
            require_success(upload, "upload document")
            document = upload.json()
            if upload.status_code != 202 or document.get("status") != "PENDING":
                raise RuntimeError(f"document was not accepted asynchronously: {document}")
            document = wait_until_ready(client, str(document["id"]))
            print(f"[2/5] Document ready: {document['id']}")

            retrieval = client.post(
                "/api/retrieval/search",
                json={"knowledge_base_id": knowledge_base_id, "query": question, "top_k": 5},
            )
            require_success(retrieval, "dense retrieval")
            retrieval_results = retrieval.json()
            if not retrieval_results:
                raise RuntimeError("retrieval returned no chunks")
            print(f"[3/5] Retrieval returned {len(retrieval_results)} chunks")

            with client.stream(
                "POST",
                "/api/chat/stream",
                json={"knowledge_base_id": knowledge_base_id, "question": question, "top_k": 5},
            ) as stream:
                require_success(stream, "stream chat")
                events = list(ui_stream_events(stream))

            event_types = [str(event.get("type")) for event in events]
            answer = "".join(
                str(event.get("delta", "")) for event in events if event.get("type") == "text-delta"
            )
            evidence_events = [event for event in events if event.get("type") == "data-retrieval"]
            if not answer.strip():
                raise RuntimeError(f"stream returned no answer text; events={event_types}")
            if not evidence_events:
                raise RuntimeError(f"stream returned no retrieval evidence; events={event_types}")
            evidence = evidence_events[0].get("data")
            if not isinstance(evidence, dict) or not evidence.get("citations"):
                raise RuntimeError("stream retrieval data contains no citations")
            if "finish" not in event_types:
                raise RuntimeError(f"stream did not finish normally; events={event_types}")
            print(f"[4/5] Stream answer and citations verified ({len(answer)} chars)")
        except Exception as exc:
            # 先保存主验收异常，仍然进入 finally 清理临时数据；清理结果不会静默覆盖根因。
            primary_error = exc
        finally:
            if knowledge_base_id is not None:
                try:
                    cleanup = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
                except httpx.HTTPError as exc:
                    cleanup_error = (
                        f"knowledge_base_id={knowledge_base_id}, cleanup request error: {exc}"
                    )
                else:
                    if cleanup.is_success:
                        print("[5/5] Temporary knowledge base deleted")
                    else:
                        cleanup_error = (
                            f"knowledge_base_id={knowledge_base_id}, cleanup HTTP "
                            f"{cleanup.status_code}: "
                            f"{cleanup.text[:500]}"
                        )

    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(f"Additional cleanup failure: {cleanup_error}")
        raise primary_error
    if cleanup_error is not None:
        raise RuntimeError(cleanup_error)


def main() -> None:
    """运行命令行验收并在成功时输出明确发布信号。"""

    arguments = parse_arguments()
    run_smoke_test(arguments.api_url, arguments.fixture, arguments.question)
    print("UltimateRAG V1 smoke test passed.")


if __name__ == "__main__":
    main()
