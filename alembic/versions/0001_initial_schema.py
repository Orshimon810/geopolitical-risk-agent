"""Initial schema -- users, embeddings, analysis_history, password_reset_tokens

Revision ID: 0001
Revises:
Create Date: 2026-06-10

Baseline migration representing the full schema from db/schema.sql.

Existing databases (created via apply_schema.py):
  alembic stamp head   -- marks DB as already at this revision, no DDL run

Fresh databases:
  alembic upgrade head -- creates all tables from scratch
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tier", sa.Text(), nullable=False, server_default="free"),
        sa.Column("daily_query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_reset_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("tier IN ('free', 'pro', 'enterprise')", name="chk_users_tier"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("idx_users_email", "users", ["email"])
    op.execute("""
        CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS geopolitical_embeddings (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            chunk_id    TEXT        NOT NULL,
            source      TEXT        NOT NULL,
            text        TEXT        NOT NULL,
            embedding   vector(1536) NOT NULL,
            metadata    JSONB       NOT NULL DEFAULT '{}',
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_embeddings_chunk_id UNIQUE (chunk_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source ON geopolitical_embeddings (source)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
        ON geopolitical_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    op.create_table(
        "analysis_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("confidence IN ('Low', 'Medium', 'High')",
                           name="chk_history_confidence"),
    )
    op.create_index("idx_history_user_created", "analysis_history", ["user_id", "created_at"])
    op.create_index("idx_history_confidence", "analysis_history", ["confidence"])
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_report_gin
        ON analysis_history USING gin (report)
    """)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token", name="uq_reset_token"),
    )
    op.create_index("idx_reset_tokens_user", "password_reset_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("analysis_history")
    op.execute("DROP TABLE IF EXISTS geopolitical_embeddings")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS fn_set_updated_at")
