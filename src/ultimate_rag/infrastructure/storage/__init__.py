"""原始对象与本地 Chunk 快照存储适配器公开入口。"""

from ultimate_rag.infrastructure.storage.chunk_snapshot import LocalChunkSnapshotStore
from ultimate_rag.infrastructure.storage.minio import MinioObjectStorage

__all__ = ["LocalChunkSnapshotStore", "MinioObjectStorage"]
