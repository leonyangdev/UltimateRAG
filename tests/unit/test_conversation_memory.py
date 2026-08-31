"""验证长会话压缩只更新派生摘要，并保留最近原文。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest

from ultimate_rag.application.chat import ConversationMemoryService
from ultimate_rag.domain.models import (
    ChatMessage,
    ChatMessageStatus,
    ChatRole,
    ChatSession,
)
from ultimate_rag.domain.ports import LLMClient
from ultimate_rag.infrastructure.database.repository import Repository


class FakeRepository:
    def __init__(self) -> None:
        self.updated_through = 0

    async def update_chat_memory(
        self,
        session_id: str,
        *,
        summary: str,
        through_sequence: int,
    ) -> ChatSession:
        self.updated_through = through_sequence
        now = datetime.now(UTC)
        return ChatSession(session_id, "kb-1", "会话", summary, through_sequence, now, now)


class FakeLLM:
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> str:
        assert "<messages>" in user_prompt
        assert max_tokens == 256
        return "用户正在比较两种架构，尚未决定。"

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        if False:
            yield ""


def _message(sequence: int, role: ChatRole, content: str) -> ChatMessage:
    now = datetime.now(UTC)
    return ChatMessage(
        id=f"message-{sequence}",
        session_id="session-1",
        sequence=sequence,
        role=role,
        status=ChatMessageStatus.COMPLETE,
        content=content,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_compacts_old_prefix_and_keeps_recent_messages_verbatim() -> None:
    now = datetime.now(UTC)
    session = ChatSession("session-1", "kb-1", "会话", "", 0, now, now)
    history = [
        _message(index, ChatRole.USER if index % 2 else ChatRole.ASSISTANT, "早期内容 " * 180)
        for index in range(1, 5)
    ]
    history.extend(
        [
            _message(5, ChatRole.USER, "它和上一种方案有什么区别？"),
            _message(6, ChatRole.ASSISTANT, "需要结合知识库继续比较。"),
        ]
    )
    repository = FakeRepository()
    service = ConversationMemoryService(
        repository=cast(Repository, repository),
        llm=cast(LLMClient, FakeLLM()),
        recent_token_budget=512,
        memory_max_tokens=256,
        tokenizer_name="cl100k_base",
    )

    context = await service.build(session, history)

    assert repository.updated_through > 0
    assert "用户正在比较两种架构" in context
    assert "它和上一种方案有什么区别" in context
    assert "最近对话原文" in context
