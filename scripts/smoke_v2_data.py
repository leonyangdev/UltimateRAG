"""使用 ``data`` 目录真实图片/PDF 验证 V2 文档智能质量。

与 ``smoke_v2.py`` 的合成格式闭环不同，本脚本专门覆盖真实示意图、15 页双栏论文、PDF 表格和
内嵌图片。它只通过公开 API 操作，验证上传立即返回、后台终态和 Milvus 实际召回，默认删除自己
创建的知识库。执行会真实调用 `.env` 中的百炼 OCR、Vision 与 Embedding，并产生模型用量。

运行：``uv run python scripts/smoke_v2_data.py --api-url http://localhost:8000``
"""

from __future__ import annotations

import argparse
import mimetypes
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from smoke_v2 import require_success, wait_until_ready


def parse_arguments() -> argparse.Namespace:
    """解析 API、样本路径和是否保留验收知识库。"""

    parser = argparse.ArgumentParser(description="Run real V2 data sample validation")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--keep", action="store_true", help="保留脚本创建的知识库供人工检查")
    return parser.parse_args()


def _sample_paths(data_dir: Path) -> list[Path]:
    """返回固定验收集并在访问 API 前报告缺失文件。"""

    samples = [data_dir / "1.png", data_dir / "2.png", data_dir / "attention is all you need.pdf"]
    missing = [str(path) for path in samples if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"V2 data samples are missing: {missing}")
    return samples


def _search(
    client: httpx.Client,
    knowledge_base_id: str,
    query: str,
    *,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """执行独立 Dense Retrieval 并验证响应是对象数组。"""

    response = client.post(
        "/api/retrieval/search",
        json={"knowledge_base_id": knowledge_base_id, "query": query, "top_k": top_k},
    )
    require_success(response, f"retrieve {query}")
    payload = response.json()
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuntimeError(f"invalid retrieval response: {payload!r}")
    return payload


def _require_document_hit(
    results: list[dict[str, Any]],
    document_id: str,
    required_terms: tuple[str, ...],
) -> dict[str, Any]:
    """要求同一召回块来自目标文档且包含全部质量标记。"""

    for result in results:
        content = str(result.get("content", ""))
        if str(result.get("document_id")) == document_id and all(
            term in content for term in required_terms
        ):
            return result
    raise RuntimeError(
        f"document {document_id} has no hit containing {required_terms}: "
        f"{[str(item.get('content', ''))[:160] for item in results]}"
    )


def run_validation(api_url: str, data_dir: Path, *, keep: bool) -> None:
    """执行异步上传、终态轮询和三类真实语义召回断言。"""

    samples = _sample_paths(data_dir)
    knowledge_base_id: str | None = None
    validation_succeeded = False
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=300.0, trust_env=False) as client:
        try:
            health = client.get("/api/health")
            require_success(health, "health check")
            created = client.post(
                "/api/knowledge-bases",
                json={
                    "name": f"V2 Real Data {uuid4().hex[:8]}",
                    "description": "scripts/smoke_v2_data.py 可回收真实样本验收。",
                },
            )
            require_success(created, "create knowledge base")
            knowledge_base_id = str(created.json()["id"])
            print(f"[1/5] Knowledge base created: {knowledge_base_id}")

            accepted: dict[str, str] = {}
            for path in samples:
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                started = time.perf_counter()
                response = client.post(
                    f"/api/knowledge-bases/{knowledge_base_id}/documents",
                    files={"file": (path.name, path.read_bytes(), mime_type)},
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                require_success(response, f"upload {path.name}")
                payload = response.json()
                if response.status_code != 202 or payload.get("status") != "PENDING":
                    raise RuntimeError(f"{path.name} did not return 202/PENDING: {payload}")
                accepted[path.name] = str(payload["id"])
                print(f"      {path.name}: 202/PENDING in {elapsed_ms:.0f} ms")
            print("[2/5] Every upload returned before parsing completed")

            for filename, document_id in accepted.items():
                payload = wait_until_ready(
                    client,
                    document_id,
                    filename,
                    timeout_seconds=1200,
                )
                print(
                    f"      {filename}: READY via "
                    f"{payload.get('parser_name')}@{payload.get('parser_version')}"
                )
            print("[3/5] Background worker completed all real samples")

            containment = _search(
                client,
                knowledge_base_id,
                "图中人工智能、机器学习和深度学习的三层嵌套包含关系是什么？",
            )
            containment_hit = _require_document_hit(
                containment,
                accepted["1.png"],
                ("深度学习", "机器学习", "人工智能", "包含关系"),
            )
            if str(containment_hit.get("content", "")).count("|  |") > 2:
                raise RuntimeError("1.png retrieval still contains empty OCR pseudo-table noise")

            architecture = _search(
                client,
                knowledge_base_id,
                "编码器怎样通过箭头连接解码器，最后怎样生成 Output Probabilities？",
            )
            _require_document_hit(
                architecture,
                accepted["2.png"],
                ("编码器", "解码器", "连接", "Output Probabilities"),
            )

            table = _search(
                client,
                knowledge_base_id,
                "What BLEU scores and training costs are reported for Transformer big in Table 2?",
            )
            table_hit = _require_document_hit(
                table,
                accepted["attention is all you need.pdf"],
                ("Table 2:", "| Model", "EN-DE", "Transformer (big)"),
            )
            locator = table_hit.get("locator")
            if not isinstance(locator, dict) or locator.get("page") != 8 or not locator.get("bbox"):
                raise RuntimeError(f"PDF table hit has no page-8 BBox: {table_hit}")
            print("[4/5] Image relations, PDF table headers and page/BBox retrieval verified")
            validation_succeeded = True
        finally:
            if knowledge_base_id is not None and not keep and validation_succeeded:
                cleanup = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
                require_success(cleanup, "cleanup knowledge base")
                print("[5/5] Temporary knowledge base deleted")
            elif knowledge_base_id is not None:
                # 失败时保留现场也避免正在处理文档触发 409 后掩盖真正的验收异常。
                print(f"[5/5] Knowledge base retained for inspection: {knowledge_base_id}")


def main() -> None:
    """运行真实样本验收并输出明确成功信号。"""

    arguments = parse_arguments()
    run_validation(arguments.api_url, arguments.data_dir, keep=arguments.keep)
    print("UltimateRAG V2 real data validation passed.")


if __name__ == "__main__":
    main()
