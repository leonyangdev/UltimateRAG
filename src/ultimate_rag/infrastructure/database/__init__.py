"""数据库引擎与 Repository 装配入口。"""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ultimate_rag.infrastructure.database.repository import Repository


def create_database(database_url: str) -> tuple[AsyncEngine, Repository]:
    """创建带连接存活检查的异步引擎及共享 Session 工厂。"""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, Repository(session_factory)


__all__ = ["Repository", "create_database"]
