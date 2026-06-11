"""Add ephemeral_embeddings table for live news cache

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11

Adds the ephemeral_embeddings table used by the live-memory news cache.
Rows are short-lived (default 48h TTL) and queried alongside the permanent
geopolitical_embeddings corpus at analysis time.

Apply:
  alembic upgrade head

Roll back:
  alembic downgrade -1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ephemeral_embeddings (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            chunk_id        TEXT        NOT NULL,
            source          TEXT        NOT NULL,
            url             TEXT        NOT NULL,
            title           TEXT        NOT NULL,
            text            TEXT        NOT NULL,
            embedding       vector(1536) NOT NULL,
            published_at    TIMESTAMPTZ NOT NULL,
            expires_at      TIMESTAMPTZ NOT NULL,
            ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_ephemeral_chunk_id UNIQUE (chunk_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ephemeral_expires
        ON ephemeral_embeddings (expires_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ephemeral_source
        ON ephemeral_embeddings (source)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ephemeral_hnsw
        ON ephemeral_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ephemeral_embeddings")
