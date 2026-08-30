"""从 PostgreSQL Chunk 事实幂等回填 V3 Milvus BM25 索引。

脚本职责：
    为 V1/V2 已经 ``READY`` 的历史文档补齐 Sparse Collection。它只读取 PostgreSQL Chunk
    并写入 Milvus 原文/BM25 Function，不读取 MinIO，也不会调用百炼 Embedding 或 LLM。

使用方式：
    ``uv run python scripts/rebuild_sparse_index.py``
    ``uv run python scripts/rebuild_sparse_index.py --knowledge-base-id <id> --replace``

安全边界：
    普通模式只做稳定 Chunk ID Upsert，可反复执行。``--replace`` 只允许配合明确知识库 ID，
    并且只删除该知识库的 Sparse 行；Dense Collection 和 PostgreSQL 事实永远不会被此脚本删除。
"""

import argparse
import asyncio

from ultimate_rag.config import get_settings
from ultimate_rag.domain.models import DocumentStatus
from ultimate_rag.infrastructure.database import create_database
from ultimate_rag.vectorstores import MilvusVectorStore


def parse_arguments() -> argparse.Namespace:
    """解析可选知识库范围、批大小与显式替换开关。"""

    parser = argparse.ArgumentParser(description="Rebuild the UltimateRAG V3 sparse index")
    parser.add_argument("--knowledge-base-id")
    parser.add_argument("--batch-size", type=int, default=500, choices=range(1, 2001))
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete this knowledge base from the sparse index before rebuilding",
    )
    arguments = parser.parse_args()
    if arguments.replace and not arguments.knowledge_base_id:
        parser.error("--replace requires --knowledge-base-id")
    return arguments


async def rebuild(
    *,
    knowledge_base_id: str | None,
    batch_size: int,
    replace: bool,
) -> int:
    """分页回填 Sparse 行并返回成功处理的 Chunk 数量。"""

    settings = get_settings()
    engine, repository = create_database(settings.database_url)
    vector_store = MilvusVectorStore(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection=settings.milvus_collection,
        sparse_collection=settings.milvus_sparse_collection,
        dimension=settings.embedding_dimension,
        bm25_k1=settings.bm25_k1,
        bm25_b=settings.bm25_b,
    )
    try:
        await vector_store.ensure_collection()
        if knowledge_base_id is not None:
            # 明确范围拼错时应立即失败，不能以“成功处理 0 条”掩盖运维参数错误。
            await repository.get_knowledge_base(knowledge_base_id)
        if replace:
            assert knowledge_base_id is not None
            documents = await repository.list_documents(knowledge_base_id)
            # 删除 Sparse 行与新摄取不是跨系统事务。禁止在同一知识库仍有运行中任务时替换，
            # 避免 Worker 已写 Sparse、尚未 READY 的短窗口被删除后又逃过本次 READY 分页。
            if any(
                document.status not in {DocumentStatus.READY, DocumentStatus.FAILED}
                for document in documents
            ):
                raise RuntimeError(
                    "cannot replace sparse index while the knowledge base has active ingestion"
                )
            await vector_store.delete_sparse_by_knowledge_base(knowledge_base_id)

        processed = 0
        after_chunk_id: str | None = None
        while True:
            chunks = await repository.list_ready_chunks_page(
                after_chunk_id=after_chunk_id,
                limit=batch_size,
                knowledge_base_id=knowledge_base_id,
            )
            if not chunks:
                return processed
            await vector_store.upsert_sparse(chunks)
            processed += len(chunks)
            after_chunk_id = chunks[-1].id
            print(f"已回填 {processed} 个 Chunk，当前游标：{after_chunk_id}")
    finally:
        await engine.dispose()


def main() -> None:
    """运行异步回填并输出最终数量。"""

    arguments = parse_arguments()
    processed = asyncio.run(
        rebuild(
            knowledge_base_id=arguments.knowledge_base_id,
            batch_size=arguments.batch_size,
            replace=arguments.replace,
        )
    )
    print(f"V3 Sparse 索引回填完成，共处理 {processed} 个 Chunk。")


if __name__ == "__main__":
    main()
