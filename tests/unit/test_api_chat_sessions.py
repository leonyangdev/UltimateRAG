"""验证聊天会话 REST 删除端点的 HTTP 边界契约。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from api.routes import delete_chat_session
from fastapi import Request, status


@pytest.mark.asyncio
async def test_delete_chat_session_returns_204_and_forwards_scope() -> None:
    """Route 应把父知识库与会话 ID 一起交给 Repository，并返回空 204。"""

    chat = SimpleNamespace(delete_session=AsyncMock())
    request = cast(
        Request,
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(chat=chat)))
        ),
    )

    response = await delete_chat_session("kb-1", "session-1", request)

    chat.delete_session.assert_awaited_once_with("kb-1", "session-1")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.body == b""
