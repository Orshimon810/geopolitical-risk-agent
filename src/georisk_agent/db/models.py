"""
SQLAlchemy 2.0 ORM models — mirrors db/schema.sql exactly.

Key design decisions:
  - Mapped[T] typed columns (SQLAlchemy 2.0 style) give IDE type-checking on ORM objects.
  - pgvector.sqlalchemy.Vector column type bridges Python lists and PG vector literals.
  - HNSW index is declared via Index(...) with postgresql_using/with/ops kwargs so that
    `Base.metadata.create_all()` can recreate it programmatically (e.g. in test fixtures).
  - JSONB used for metadata and report fields to support GIN indexing and path queries.
  - `expire_on_commit=False` in the session factory means objects remain readable after
    commit without triggering lazy loads (critical in async contexts).
"""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# =============================================================================
# Users
# =============================================================================

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "tier IN ('free', 'pro', 'enterprise')",
            name="chk_users_tier",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(254), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    analyses: Mapped[list["AnalysisHistory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",  # never auto-load in async context; use explicit joinedload()
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} tier={self.tier!r}>"


# =============================================================================
# Geopolitical Embeddings
# =============================================================================

class GeopoliticalEmbedding(Base):
    __tablename__ = "geopolitical_embeddings"
    __table_args__ = (
        # HNSW index for approximate nearest-neighbor search (cosine distance).
        # vector_cosine_ops instructs pgvector to use the <=> (cosine distance)
        # operator for index traversal.
        #
        # m=16, ef_construction=64 are the pgvector defaults and work well for
        # corpora up to ~1M vectors. For 100k+ vectors consider m=24, ef=128.
        Index(
            "idx_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Stable deduplication key — sha256(text) for new ingestions.
    # ON CONFLICT on this column enables idempotent re-ingestion.
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Vector(1536) maps to PG type `vector(1536)`.
    # SQLAlchemy sends Python list[float] ↔ PG vector literal automatically.
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    # "metadata" is a reserved keyword in SQLAlchemy Base; use key= to remap.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<GeopoliticalEmbedding chunk_id={self.chunk_id!r} source={self.source!r}>"
        )


# =============================================================================
# Analysis History
# =============================================================================

class AnalysisHistory(Base):
    __tablename__ = "analysis_history"
    __table_args__ = (
        CheckConstraint(
            "confidence IN ('Low', 'Medium', 'High')",
            name="chk_history_confidence",
        ),
        # Composite index: all reports for a user, newest first.
        # Covers the paginated /history endpoint entirely (index-only scan).
        Index("idx_history_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    # Full AnalysisOutput.model_dump() dict — includes market_impacts, risks,
    # scenarios, investor_takeaway, confidence, sources, reasoning.
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped["User | None"] = relationship(
        back_populates="analyses",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<AnalysisHistory id={self.id} confidence={self.confidence!r} "
            f"user_id={self.user_id}>"
        )


# =============================================================================
# Ephemeral News Embeddings
# =============================================================================

class EphemeralNewsEmbedding(Base):
    __tablename__ = "ephemeral_embeddings"
    __table_args__ = (
        Index(
            "idx_ephemeral_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_ephemeral_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # sha256(url) — dedup key so re-ingesting the same article URL is idempotent
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # news outlet name
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)  # title + description/summary
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<EphemeralNewsEmbedding source={self.source!r} title={self.title[:40]!r}>"


# =============================================================================
# Password Reset Tokens
# =============================================================================

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<PasswordResetToken user_id={self.user_id} used={self.used_at is not None}>"
