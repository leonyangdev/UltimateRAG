"""本地 JSON Chunk 快照存储适配器。

模块职责：
    把完成切分且已补齐最终 metadata 的 Chunk 保存为人类可读的 UTF-8 JSON，并在文档或
    知识库删除时清理对应的本地明文副本。

架构边界：
    本模块实现领域层 ``ChunkSnapshotStore`` 端口，只负责本地文件系统持久化；它不执行
    Parsing、Chunking、Embedding，也不替代 PostgreSQL 事实数据或 Milvus 派生索引。

设计背景：
    快照写在 Embedding 之前，用于直接检查“模型真正收到什么文本和 metadata”。同一文档
    重试或重新解析会覆盖稳定路径，避免生成无界历史副本。写入使用同目录临时文件和
    ``os.replace`` 原子发布，Worker 中断时不会把半截 JSON 暴露为有效快照。

典型调用位置：
    ``DocumentProcessingService`` 在 Asset 持久化与 Chunk metadata 补齐之后调用 ``save``。

重要约束：
    快照包含原始 Chunk 明文，必须按敏感业务数据保护。文件 I/O 通过 ``asyncio.to_thread``
    移出事件循环，避免大文档写盘阻塞 Worker 心跳；快照不包含体积大且可重建的向量。
"""

import asyncio
import json
import os
import re
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ultimate_rag.domain.models import Chunk, Document, JsonValue, ParsedDocument


class LocalChunkSnapshotStore:
    """使用稳定目录和原子替换保存本地 Chunk JSON 快照。

    实例是无状态且可复用的，除根目录外不持有文件句柄。路径只使用系统生成的知识库和
    文档 ID，不使用不可信的上传文件名；删除操作还会验证目标始终位于配置根目录内。
    """

    _SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
    _SCHEMA_VERSION = 1
    _FILENAME = "chunks.json"

    def __init__(self, root_directory: str | Path) -> None:
        """配置宿主机或容器挂载目录。

        Args:
            root_directory: 快照根目录。本地默认是 ``data/chunk_snapshots``；Docker Worker
                使用绑定挂载后的 ``/app/data/chunk_snapshots``。
        """

        self._root_directory = Path(root_directory).expanduser().resolve(strict=False)

    async def save(
        self,
        *,
        document: Document,
        parsed_document: ParsedDocument,
        parser_name: str,
        parser_version: str,
        chunks: Sequence[Chunk],
    ) -> None:
        """原子保存最终 Chunk 与关联的解析 metadata。

        Args:
            document: PostgreSQL 中的文档事实，用于来源标识和安全目录分级。
            parsed_document: Parser 的统一输出；这里只保存顶层 metadata，不复制 Block 或
                Asset 二进制，避免和 Chunk/MinIO 重复存储。
            parser_name: 本次实际选中的 Parser 名称。
            parser_version: 本次实际选中的 Parser 版本。
            chunks: 已补齐文件名、SourceLocator 等最终 metadata 的有序 Chunk。

        Raises:
            ValueError: 系统 ID 不符合安全路径片段约束。
            OSError: 目录创建、文件写入或原子替换失败。
            TypeError: metadata 不满足领域模型声明的 JSON 可序列化约束。

        Side Effects:
            写入 ``<root>/<knowledge_base_id>/<document_id>/chunks.json``。同一文档重试时
            原子覆盖旧文件；异常时旧快照保持完整，并清理本次临时文件。
        """

        # JSON 只保留可读、可追溯的 Chunk 数据，不复制 Embedding。向量既体积大又与供应商
        # 模型绑定，且当前保存时机本来就位于 Embedding 之前。
        payload = self._build_payload(
            document=document,
            parsed_document=parsed_document,
            parser_name=parser_name,
            parser_version=parser_version,
            chunks=chunks,
        )

        # 标准文件 API 是阻塞 I/O。移入线程后，即使快照较大，Worker 的 asyncio 心跳仍可
        # 按时续租，避免“只是本地写盘”却被误判为处理进程失联。
        await asyncio.to_thread(
            self._write_atomic,
            document.knowledge_base_id,
            document.id,
            payload,
        )

    async def delete_by_document(self, knowledge_base_id: str, document_id: str) -> None:
        """幂等删除一份文档快照，并清理空的知识库目录。

        快照包含业务明文，因此文档删除不能只清理 PostgreSQL、MinIO 和 Milvus。删除失败
        会向应用层抛出，后者保留 PostgreSQL 事实以便运维继续补偿。
        """

        await asyncio.to_thread(self._delete_document, knowledge_base_id, document_id)

    async def delete_by_knowledge_base(self, knowledge_base_id: str) -> None:
        """幂等删除一个知识库下的全部本地 Chunk 快照。"""

        await asyncio.to_thread(self._delete_knowledge_base, knowledge_base_id)

    @classmethod
    def _build_payload(
        cls,
        *,
        document: Document,
        parsed_document: ParsedDocument,
        parser_name: str,
        parser_version: str,
        chunks: Sequence[Chunk],
    ) -> dict[str, JsonValue]:
        """把领域对象转换为版本化且不含基础设施对象的 JSON 数据。"""

        serialized_chunks = list[JsonValue](
            {
                "id": chunk.id,
                "knowledge_base_id": chunk.knowledge_base_id,
                "document_id": chunk.document_id,
                "index": chunk.index,
                "content": chunk.content,
                "heading_path": list(chunk.heading_path),
                "token_count": chunk.token_count,
                "locator": chunk.locator.to_metadata() if chunk.locator else None,
                "metadata": dict(chunk.metadata),
            }
            for chunk in chunks
        )
        return {
            "schema_version": cls._SCHEMA_VERSION,
            "snapshot_stage": "post_chunk_pre_embedding",
            "written_at": datetime.now(UTC).isoformat(),
            "document": {
                "id": document.id,
                "knowledge_base_id": document.knowledge_base_id,
                "filename": document.filename,
                "mime_type": document.mime_type,
                "extension": document.extension,
                "object_key": document.object_key,
                "sha256": document.sha256,
                "parser_name": parser_name,
                "parser_version": parser_version,
            },
            "parsed_metadata": dict(parsed_document.metadata),
            "chunk_count": len(chunks),
            "chunks": serialized_chunks,
        }

    def _write_atomic(
        self,
        knowledge_base_id: str,
        document_id: str,
        payload: dict[str, JsonValue],
    ) -> None:
        """在同目录完成临时写入、关闭文件和原子发布。"""

        document_directory = self._document_directory(knowledge_base_id, document_id)
        document_directory.mkdir(parents=True, exist_ok=True)
        target_path = document_directory / self._FILENAME
        temporary_path = document_directory / f".{self._FILENAME}.{uuid4().hex}.tmp"

        try:
            # ``x`` 模式让极小概率的临时名冲突显式失败；UTF-8 与 ensure_ascii=False 使中文
            # 内容无需反解转义即可人工审查，indent=2 则便于 Git 外部工具直接比较。
            with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()

            # 文件关闭后 Python 缓冲区已经交给操作系统；临时文件与目标又位于同一目录，
            # os.replace 可原子切换名称。这里不强制 fsync：它在部分 Windows/绑定挂载上会让
            # 每个文档额外阻塞数十秒，而快照是可重建诊断副本，不是数据库提交日志。
            os.replace(temporary_path, target_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _delete_document(self, knowledge_base_id: str, document_id: str) -> None:
        """同步删除已验证位于根目录下的文档目录。"""

        document_directory = self._document_directory(knowledge_base_id, document_id)
        if document_directory.exists():
            shutil.rmtree(document_directory)

        knowledge_base_directory = self._knowledge_base_directory(knowledge_base_id)
        if knowledge_base_directory.exists() and not any(knowledge_base_directory.iterdir()):
            knowledge_base_directory.rmdir()

    def _delete_knowledge_base(self, knowledge_base_id: str) -> None:
        """同步删除已验证位于根目录下的知识库目录。"""

        knowledge_base_directory = self._knowledge_base_directory(knowledge_base_id)
        if knowledge_base_directory.exists():
            shutil.rmtree(knowledge_base_directory)

    def _document_directory(self, knowledge_base_id: str, document_id: str) -> Path:
        """构造并验证文档快照目录，禁止任意路径片段逃逸。"""

        knowledge_base_directory = self._knowledge_base_directory(knowledge_base_id)
        safe_document_id = self._safe_id(document_id, "document_id")
        return self._within_root(knowledge_base_directory / safe_document_id)

    def _knowledge_base_directory(self, knowledge_base_id: str) -> Path:
        """构造并验证知识库级目录。"""

        safe_knowledge_base_id = self._safe_id(knowledge_base_id, "knowledge_base_id")
        return self._within_root(self._root_directory / safe_knowledge_base_id)

    @classmethod
    def _safe_id(cls, value: str, field_name: str) -> str:
        """只允许 UUID 兼容的安全目录字符，不接受斜杠、点号或空白。"""

        if not cls._SAFE_ID.fullmatch(value):
            raise ValueError(f"{field_name} 不能安全用作 Chunk 快照目录")
        return value

    def _within_root(self, candidate: Path) -> Path:
        """防御性验证删除/写入目标始终位于配置根目录之下。"""

        resolved = candidate.resolve(strict=False)
        if self._root_directory not in resolved.parents:
            raise ValueError("Chunk 快照目标目录超出配置根目录")
        return resolved
