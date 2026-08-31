"""持久化文档抽取资源与历史会话检索证据。

Revision ID: 0004_document_assets
Revises: 0003_chat_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_document_assets"
down_revision: str | None = "0003_chat_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建文档资源事实表，并让历史助手消息保存检索证据快照。"""

    op.create_table(
        "document_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("block_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_document_assets_document_id", "document_assets", ["document_id"])
    op.create_index("ix_document_assets_block_id", "document_assets", ["block_id"])
    op.add_column(
        "chat_messages",
        sa.Column(
            "retrieval_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """移除资源与会话证据能力，不修改原文档、Chunk 或消息正文。"""

    op.drop_column("chat_messages", "retrieval_evidence")
    op.drop_index("ix_document_assets_block_id", table_name="document_assets")
    op.drop_index("ix_document_assets_document_id", table_name="document_assets")
    op.drop_table("document_assets")
