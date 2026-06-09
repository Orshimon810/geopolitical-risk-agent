"""
Data Access Layer (DAL) — all PostgreSQL interactions for the geopolitical risk agent.

Design principles:
  - Every function accepts an AsyncSession as its first argument.
    This makes functions composable (multiple DAL calls share one transaction) and
    easy to inject via FastAPI's Depends() without coupling to the session lifecycle.
  - Bulk operations use PostgreSQL's INSERT ... ON CONFLICT DO UPDATE (upsert) so
    re-ingestion of the same document corpus is always idempotent.
  - Semantic search uses raw SQL text() for the pgvector <=> operator; SQLAlchemy's
    ORM query builder doesn't natively know this operator, and raw SQL is clearer here.
  - Password hashing uses bcrypt directly — never store or compare plaintext passwords.
  - All public functions are async; none block the event loop.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import bcrypt as _bcrypt
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from georisk_agent.db.models import AnalysisHistory, GeopoliticalEmbedding, User

logger = logging.getLogger(__name__)

# =============================================================================
# Password utilities (stateless — no session needed)
# =============================================================================

def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash (12 rounds). Never store plaintext."""
    return _bcrypt.hashpw(plaintext.encode(), _bcrypt.gensalt(rounds=12)).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True iff plaintext matches the stored bcrypt hash."""
    return _bcrypt.checkpw(plaintext.encode(), hashed.encode())


# =============================================================================
# User management
# =============================================================================

async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_plaintext: str,
    full_name: str | None = None,
    tier: str = "free",
) -> User:
    """
    Create a new user with a bcrypt-hashed password.
    Raises sqlalchemy.exc.IntegrityError if the email is already registered.
    Call session.flush() first if you need the id before commit.
    """
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password_plaintext),
        full_name=full_name,
        tier=tier,
    )
    session.add(user)
    await session.flush()  # populates user.id without committing the outer transaction
    logger.info("Created user email=%r tier=%r", user.email, user.tier)
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(
        select(User).where(User.email == email.lower().strip())
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def authenticate_user(
    session: AsyncSession, email: str, password_plaintext: str
) -> User | None:
    """
    Return the User if credentials are valid, None otherwise.
    Always runs the bcrypt check even when the user doesn't exist (timing-safe).
    """
    user = await get_user_by_email(session, email)
    if user is None:
        # Dummy verify to normalise response time and prevent user-enumeration.
        _bcrypt.checkpw(b"dummy", _bcrypt.hashpw(b"dummy", _bcrypt.gensalt()))
        return None
    if not verify_password(password_plaintext, user.password_hash):
        return None
    return user


# =============================================================================
# Embeddings — ingestion (upsert) + semantic retrieval
# =============================================================================

async def upsert_embedding(
    session: AsyncSession,
    *,
    chunk_id: str,
    source: str,
    text_content: str,
    embedding: list[float],
    metadata: dict[str, Any] | None = None,
) -> GeopoliticalEmbedding:
    """
    Insert or update a single embedding chunk.

    ON CONFLICT on chunk_id updates text, embedding, and metadata in place —
    safe to call repeatedly during incremental re-ingestion.
    """
    stmt = (
        pg_insert(GeopoliticalEmbedding)
        .values(
            chunk_id=chunk_id,
            source=source,
            text=text_content,
            embedding=embedding,
            metadata_=metadata or {},
        )
        .on_conflict_do_update(
            index_elements=["chunk_id"],
            set_={
                "source": source,
                "text": text_content,
                "embedding": embedding,
                "metadata": metadata or {},  # DB column name as key (not the Python attr metadata_)
                "ingested_at": datetime.now(timezone.utc),
            },
        )
        .returning(GeopoliticalEmbedding)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def bulk_upsert_embeddings(
    session: AsyncSession,
    records: list[dict[str, Any]],
    batch_size: int = 64,
) -> int:
    """
    Efficiently upsert many embedding records in sequential batches.

    Each record must be a dict with keys:
        chunk_id (str), source (str), text (str),
        embedding (list[float], dim=1536), metadata (dict, optional)

    Returns the total number of records processed.

    Batch size of 64 matches the ChromaDB ingest batch size and keeps individual
    PG statements under the ~32k parameter limit (64 records × ~25 params each).
    """
    total = 0
    for batch_start in range(0, len(records), batch_size):
        batch = records[batch_start : batch_start + batch_size]

        values = [
            {
                "chunk_id": r["chunk_id"],
                "source": r["source"],
                "text": r["text"],
                "embedding": r["embedding"],
                "metadata_": r.get("metadata", {}),
            }
            for r in batch
        ]

        # Build insert + upsert from the same `ins` object so that `ins.excluded`
        # refers to the correct statement. Access the `metadata` column by its
        # DB column name (not the Python attribute name `metadata_`) to avoid
        # the KeyError that occurs when SQLAlchemy resolves excluded by column name.
        ins = pg_insert(GeopoliticalEmbedding).values(values)
        stmt = ins.on_conflict_do_update(
            index_elements=["chunk_id"],
            set_={
                "source": ins.excluded.source,
                "text": ins.excluded.text,
                "embedding": ins.excluded.embedding,
                "metadata": ins.excluded["metadata"],
                "ingested_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)
        total += len(batch)
        logger.debug(
            "Upserted embeddings batch [%d–%d] / %d",
            batch_start,
            batch_start + len(batch),
            len(records),
        )

    return total


async def semantic_search(
    session: AsyncSession,
    query_embedding: list[float],
    k: int = 5,
    min_similarity: float = 0.0,
    source_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve the top-k most semantically similar chunks using cosine similarity.

    Internals:
      - <=> is the pgvector cosine *distance* operator (0 = identical, 2 = opposite).
      - similarity = 1 − cosine_distance, so higher = more similar (range: −1 to 1,
        practically 0 to 1 for non-negative embedding spaces like OpenAI's).
      - The HNSW index on `embedding vector_cosine_ops` is picked up automatically
        by the ORDER BY embedding <=> :vec clause.

    Increasing recall at query time (without rebuilding the index):
        SET LOCAL hnsw.ef_search = 100;   -- default 40; higher = slower but more accurate
    Run this inside the same session before calling this function for high-stakes queries.

    Args:
        query_embedding:  1536-dim float list from OpenAI embeddings API.
        k:                Maximum number of results to return.
        min_similarity:   Float in [0, 1]. Filters out chunks below this threshold.
                          0.0 returns all top-k; 0.7+ is a reasonable relevance gate.
        source_filter:    If set, restricts results to chunks from this source document.

    Returns:
        List of dicts: {chunk_id, source, text, metadata, similarity}
        Sorted by similarity descending (most relevant first).
    """
    # Serialise embedding as a pgvector literal: [0.1,0.2,...,0.9]
    vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"

    source_clause = "AND source = :source" if source_filter else ""
    params: dict[str, Any] = {
        "vec": vec_literal,
        "k": k,
        "min_sim": min_similarity,
    }
    if source_filter:
        params["source"] = source_filter

    # asyncpg chokes on :param::type (the double-colon after a named param
    # is ambiguous). Use CAST(:param AS type) for the explicit cast instead.
    sql = text(f"""
        SELECT
            chunk_id,
            source,
            text,
            metadata,
            1 - (embedding <=> CAST(:vec AS vector))  AS similarity
        FROM
            geopolitical_embeddings
        WHERE
            1 - (embedding <=> CAST(:vec AS vector)) >= :min_sim
            {source_clause}
        ORDER BY
            embedding <=> CAST(:vec AS vector)
        LIMIT :k
    """)

    result = await session.execute(sql, params)
    rows = result.mappings().all()

    return [
        {
            "chunk_id": row["chunk_id"],
            "source": row["source"],
            "text": row["text"],
            "metadata": row["metadata"],
            "similarity": float(row["similarity"]),
        }
        for row in rows
    ]


# =============================================================================
# Analysis history
# =============================================================================

async def save_analysis(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    query: str,
    report: dict[str, Any],
    confidence: str,
    model_name: str | None = None,
    tokens_used: int | None = None,
    duration_ms: int | None = None,
) -> AnalysisHistory:
    """
    Persist a completed investment analysis report.

    user_id may be None for anonymous CLI invocations or evaluation runs.
    report should be AnalysisOutput.model_dump() — the full structured output
    including market_impacts, risks, scenarios, investor_takeaway, and sources.
    """
    record = AnalysisHistory(
        user_id=user_id,
        query=query,
        report=report,
        confidence=confidence,
        model_name=model_name,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
    )
    session.add(record)
    await session.flush()
    logger.info(
        "Saved analysis id=%s confidence=%r user=%s",
        record.id,
        confidence,
        user_id,
    )
    return record


async def get_user_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[AnalysisHistory]:
    """
    Fetch a paginated list of analysis reports for a user, newest first.

    Example (page 2 of 20-item pages):
        records = await get_user_history(session, user_id, limit=20, offset=20)
    """
    result = await session.execute(
        select(AnalysisHistory)
        .where(AnalysisHistory.user_id == user_id)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_analysis_by_id(
    session: AsyncSession,
    analysis_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> AnalysisHistory | None:
    """
    Fetch a single analysis report by its UUID.
    If user_id is provided, enforces ownership — returns None if the record
    exists but belongs to a different user (prevents IDOR).
    """
    stmt = select(AnalysisHistory).where(AnalysisHistory.id == analysis_id)
    if user_id is not None:
        stmt = stmt.where(AnalysisHistory.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def delete_analysis_by_id(
    session: AsyncSession,
    analysis_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Delete a single analysis by ID, enforcing ownership. Returns True if deleted."""
    result = await session.execute(
        delete(AnalysisHistory)
        .where(AnalysisHistory.id == analysis_id)
        .where(AnalysisHistory.user_id == user_id)
    )
    return result.rowcount > 0  # type: ignore[return-value]


async def delete_user_history(
    session: AsyncSession, user_id: uuid.UUID
) -> int:
    """Delete all analysis history for a user. Returns number of deleted rows."""
    result = await session.execute(
        delete(AnalysisHistory).where(AnalysisHistory.user_id == user_id)
    )
    return result.rowcount  # type: ignore[return-value]
