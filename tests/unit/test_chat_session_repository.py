"""验证聊天会话删除的知识库边界与消息级联约束。

这些测试只替换 SQLAlchemy Session 的事务外壳，不连接真实 PostgreSQL。测试重点是
Repository 的业务分支以及 ORM/DDL 中必须长期保持的级联声明；真正的外键行为由
Alembic 创建的 PostgreSQL ``ON DELETE CASCADE`` 执行。
"""

from typing import cast

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ultimate_rag.domain.exceptions import ChatSessionBusyError, ResourceNotFoundError
from ultimate_rag.infrastructure.database.models import (
    ChatMessageModel,
    ChatSessionModel,
    KnowledgeBaseModel,
)
from ultimate_rag.infrastructure.database.repository import Repository


class _TransactionContext:
    """提供测试所需的异步事务协议，不模拟数据库提交细节。"""

    async def __aenter__(self) -> None:
        """进入事务上下文。"""

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        """退出事务上下文；异常继续由被测方法向外传播。"""


class _Session:
    """记录 Repository 对 Session 的关键调用和删除目标。"""

    def __init__(
        self,
        *,
        knowledge_base: KnowledgeBaseModel | None,
        chat_session: ChatSessionModel | None,
        active_pending_message_id: str | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.chat_session = chat_session
        self.active_pending_message_id = active_pending_message_id
        self.scalar_calls = 0
        self.deleted: list[object] = []

    async def __aenter__(self) -> "_Session":
        """把当前测试 Session 交给 Repository。"""

        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        """退出 Session 上下文。"""

    def begin(self) -> _TransactionContext:
        """返回与 ``session.begin()`` 兼容的异步上下文。"""

        return _TransactionContext()

    async def get(self, model_type: type[object], identity: str) -> object | None:
        """仅模拟被测方法的知识库存在性读取。"""

        assert model_type is KnowledgeBaseModel
        if self.knowledge_base is None or self.knowledge_base.id != identity:
            return None
        return self.knowledge_base

    async def scalar(self, statement: object) -> ChatSessionModel | None:
        """记录范围查询，并返回预设的会话结果。"""

        self.scalar_calls += 1
        if self.scalar_calls == 1:
            # 编译参数能证明父会话查询同时携带会话和知识库 ID，避免测试只验证
            # “最后删了对象”，却没有覆盖防止跨知识库删除的核心范围约束。
            compiled_parameters = statement.compile().params  # type: ignore[attr-defined]
            assert set(compiled_parameters.values()) == {"kb-1", "session-1"}
            return self.chat_session
        return cast(ChatSessionModel | None, self.active_pending_message_id)

    async def delete(self, model: object) -> None:
        """记录事务最终删除的父会话。"""

        self.deleted.append(model)


class _SessionFactory:
    """让固定测试 Session 具备 ``async_sessionmaker`` 的可调用形态。"""

    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self) -> _Session:
        """每次 Repository 操作返回同一个受控 Session。"""

        return self.session


def _repository(session: _Session) -> Repository:
    """构造 Repository，并把窄测试替身适配到生产构造参数类型。"""

    return Repository(
        cast(async_sessionmaker[AsyncSession], cast(object, _SessionFactory(session)))
    )


def _knowledge_base() -> KnowledgeBaseModel:
    """创建足以参与删除校验的知识库 ORM 对象。"""

    return KnowledgeBaseModel(id="kb-1", name="知识库", description="")


def _chat_session() -> ChatSessionModel:
    """创建属于 ``kb-1`` 的会话 ORM 对象。"""

    return ChatSessionModel(id="session-1", knowledge_base_id="kb-1")


@pytest.mark.asyncio
async def test_delete_chat_session_scopes_query_and_deletes_parent() -> None:
    """合法删除必须同时约束知识库与会话，并只显式删除父会话。"""

    chat_session = _chat_session()
    session = _Session(knowledge_base=_knowledge_base(), chat_session=chat_session)

    await _repository(session).delete_chat_session(
        "kb-1", "session-1", stale_after_seconds=600
    )

    assert session.scalar_calls == 2
    assert session.deleted == [chat_session]


@pytest.mark.asyncio
async def test_delete_chat_session_rejects_missing_knowledge_base() -> None:
    """父知识库不存在时应返回明确业务错误，并且不能继续查询或删除会话。"""

    session = _Session(knowledge_base=None, chat_session=_chat_session())

    with pytest.raises(ResourceNotFoundError, match="知识库不存在"):
        await _repository(session).delete_chat_session(
            "kb-1", "session-1", stale_after_seconds=600
        )

    assert session.scalar_calls == 0
    assert session.deleted == []


@pytest.mark.asyncio
async def test_delete_chat_session_hides_missing_or_out_of_scope_session() -> None:
    """范围查询没有结果时统一按会话不存在处理，不泄漏其他知识库的数据。"""

    session = _Session(knowledge_base=_knowledge_base(), chat_session=None)

    with pytest.raises(ResourceNotFoundError, match="会话不存在"):
        await _repository(session).delete_chat_session(
            "kb-1", "session-1", stale_after_seconds=600
        )

    assert session.deleted == []


@pytest.mark.asyncio
async def test_delete_chat_session_rejects_active_generation() -> None:
    """有效 PENDING 助手消息必须阻止删除，避免流结束时提交到已删除记录。"""

    session = _Session(
        knowledge_base=_knowledge_base(),
        chat_session=_chat_session(),
        active_pending_message_id="message-pending",
    )

    with pytest.raises(ChatSessionBusyError, match="正在生成回答"):
        await _repository(session).delete_chat_session(
            "kb-1", "session-1", stale_after_seconds=600
        )

    assert session.deleted == []


def test_chat_message_relationship_keeps_both_cascade_guards() -> None:
    """会话删除必须保留 ORM delete-orphan 与数据库 ON DELETE CASCADE 双重保障。"""

    relationship = inspect(ChatSessionModel).relationships.messages
    foreign_key = next(iter(ChatMessageModel.__table__.c.session_id.foreign_keys))

    assert "delete" in relationship.cascade
    assert "delete-orphan" in relationship.cascade
    assert foreign_key.ondelete == "CASCADE"
