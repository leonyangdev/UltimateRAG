"""MinIO 原始文件与文档 Asset 存储适配器。

MinIO Python SDK 是同步接口，因此所有网络操作都移入工作线程，避免阻塞 FastAPI 事件循环。
"""

import asyncio
from io import BytesIO

from minio import Minio


class MinioObjectStorage:
    """以明确 Bucket 和系统对象键实现 ``ObjectStorage`` 端口。"""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        """创建固定 Endpoint/Bucket 的 MinIO 客户端；凭据仅保存在 SDK 内部。"""
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket

    async def ensure_bucket(self) -> None:
        """幂等创建原始文档与抽取 Asset 共用的私有 Bucket。"""

        def ensure() -> None:
            """在工作线程中调用同步 SDK 检查并创建 Bucket。"""
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await asyncio.to_thread(ensure)

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        """上传内存中的原始文档或受限 Asset，并保存标准 MIME 类型。"""
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_key,
            BytesIO(content),
            len(content),
            content_type=content_type,
        )

    async def get(self, object_key: str) -> bytes:
        """读取对象并始终关闭 HTTP 响应，防止连接池泄漏。"""

        def read() -> bytes:
            """在工作线程中完整读取对象并可靠释放 HTTP 连接。"""
            response = self._client.get_object(self._bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(read)

    async def delete(self, object_key: str) -> None:
        """删除指定对象；目标键只能由应用生成，不能直接来自用户文件名。"""
        await asyncio.to_thread(self._client.remove_object, self._bucket, object_key)
