"""验证 FastAPI 上传边界不会把无界请求完整读入应用内存。"""

from io import BytesIO

import pytest
from api.routes import _read_bounded_upload
from fastapi import UploadFile

from ultimate_rag.domain.exceptions import InvalidDocumentError


@pytest.mark.asyncio
async def test_bounded_upload_accepts_content_at_limit() -> None:
    """恰好达到上限的文件仍应完整交给应用摄取服务。"""

    upload = UploadFile(filename="valid.md", file=BytesIO(b"12345"))

    assert await _read_bounded_upload(upload, max_upload_bytes=5) == b"12345"


@pytest.mark.asyncio
async def test_bounded_upload_rejects_after_reading_only_one_extra_byte() -> None:
    """超限检测只需读取上限外一个字节，不应继续消费后续上传内容。"""

    upload = UploadFile(filename="oversized.md", file=BytesIO(b"123456789"))

    with pytest.raises(InvalidDocumentError, match="文件不能超过"):
        await _read_bounded_upload(upload, max_upload_bytes=5)

    # 文件指针停在 max + 1，证明 HTTP 边界没有先读取整个九字节请求再执行大小校验。
    assert upload.file.tell() == 6
