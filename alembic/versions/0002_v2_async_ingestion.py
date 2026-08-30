"""Add the durable V2 background-ingestion queue.

Revision ID: 0002_v2_async_ingestion
Revises: 0001_v1_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_v2_async_ingestion"
down_revision: str | None = "0001_v1_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建持久化任务表，并恢复旧版本中断后遗留的非终态文档。"""

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["status", "available_at"],
    )

    # V1/V2 旧同步进程若在处理中退出，文档可能永久停在 PARSING 等状态。任务 ID 直接复用
    # document_id，既无需数据库扩展生成 UUID，也能保证这次迁移可重复理解和追踪。
    op.execute(
        sa.text(
            """
            INSERT INTO ingestion_jobs (
                id, document_id, status, attempts, max_attempts,
                available_at, locked_at, worker_id, error_message, created_at, updated_at
            )
            SELECT
                id, id, 'PENDING', 0, 3,
                CURRENT_TIMESTAMP, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM documents
            WHERE status IN ('PENDING', 'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET status = 'PENDING',
                error_message = '服务升级后已恢复后台处理任务',
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING')
            """
        )
    )


def downgrade() -> None:
    """删除任务表；文档与 Chunk 事实保持不变。"""

    op.drop_index("ix_ingestion_jobs_claim", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
