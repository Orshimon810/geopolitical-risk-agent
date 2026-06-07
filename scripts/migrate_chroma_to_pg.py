#!/usr/bin/env python3
"""
Migration: ChromaDB → PostgreSQL (pgvector)

What this script does:
  1. Opens the local ChromaDB PersistentClient at CHROMA_DIR.
  2. Fetches ALL records including stored embeddings in batches.
  3. Upserts them into the `geopolitical_embeddings` table via bulk_upsert_embeddings.

Embeddings are reused directly from ChromaDB — no re-embedding API calls, no cost.
If ChromaDB didn't persist embeddings for a collection (rare but possible if the
collection was created without an embedding_function), the script falls back to
re-embedding via the OpenAI API automatically.

Usage:
    # Preview without writing to the database
    python scripts/migrate_chroma_to_pg.py --dry-run

    # Full migration in batches of 64
    python scripts/migrate_chroma_to_pg.py

    # Custom batch size (e.g. for very large corpora to control memory)
    python scripts/migrate_chroma_to_pg.py --batch-size 128

    # Migrate a non-default collection name
    python scripts/migrate_chroma_to_pg.py --collection my_collection

Environment (all read from .env):
    OPENAI_API_KEY   Required to open the Chroma collection (embedding function)
                     and for fallback re-embedding if stored vectors are missing.
    CHROMA_DIR       Path to the ChromaDB persist directory (default: .chroma)
    DATABASE_URL     Target PostgreSQL DSN:
                     postgresql+asyncpg://user:pass@host:5432/dbname
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# Make src/ importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from georisk_agent.app.config import settings
from georisk_agent.db.client import close_engine, get_session
from georisk_agent.db.dal import bulk_upsert_embeddings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("migrate_chroma_to_pg")

# How many records to fetch from Chroma in one get() call.
# Chroma loads everything into memory, so keep this reasonable for large corpora.
CHROMA_FETCH_BATCH = 2000


def _open_collection(collection_name: str):
    """Open the ChromaDB collection. Raises if CHROMA_DIR doesn't exist."""
    from chromadb import PersistentClient
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

    chroma_dir = Path(settings.chroma_dir)
    if not chroma_dir.exists():
        raise FileNotFoundError(
            f"ChromaDB directory not found: {chroma_dir.resolve()}\n"
            "Run `python scripts/ingest_documents.py` first to populate it."
        )

    client = PersistentClient(path=str(chroma_dir))
    embedding_fn = OpenAIEmbeddingFunction(
        api_key=settings.openai_api_key,
        model_name="text-embedding-3-small",
    )
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
    )
    logger.info(
        "Opened ChromaDB collection %r at %s (count=%d)",
        collection_name,
        chroma_dir.resolve(),
        collection.count(),
    )
    return collection


def _re_embed(texts: list[str]) -> list[list[float]]:
    """
    Fallback: call the OpenAI embeddings API to (re-)embed a batch of texts.
    Used only when ChromaDB didn't persist the original vectors.
    """
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    logger.info("Re-embedding %d texts via OpenAI (fallback path)…", len(texts))
    response = client.embeddings.create(
        input=texts,
        model="text-embedding-3-small",
    )
    return [item.embedding for item in response.data]


def fetch_all_records(collection, re_embed_missing: bool = True) -> list[dict[str, Any]]:
    """
    Pull every record from ChromaDB with its stored embedding.

    ChromaDB's get() with include=["embeddings"] returns the raw float32 arrays
    that were stored at ingestion time — no API call needed.

    If embeddings are absent (None or mismatched count), falls back to re-embedding
    when re_embed_missing=True; otherwise raises ValueError.

    Returns a flat list of dicts ready for bulk_upsert_embeddings().
    """
    total = collection.count()
    if total == 0:
        logger.warning("ChromaDB collection is empty — nothing to migrate.")
        return []

    logger.info("Fetching %d records from ChromaDB (this may take a moment)…", total)

    # Chroma's get() returns all records at once; for very large collections
    # (>100k) consider chunking with `offset` and `limit` parameters.
    raw = collection.get(include=["embeddings", "documents", "metadatas"])

    ids: list[str] = raw.get("ids", [])
    docs: list[str] = raw.get("documents", [])
    metas: list[dict] = raw.get("metadatas", [])
    embeddings: list | None = raw.get("embeddings")

    if not ids:
        logger.warning("get() returned no IDs — collection may be empty.")
        return []

    missing_embeddings = (
        embeddings is None
        or len(embeddings) != len(ids)
        or any(e is None for e in embeddings)
    )

    if missing_embeddings:
        if not re_embed_missing:
            raise ValueError(
                f"ChromaDB returned {len(embeddings) if embeddings else 0} embeddings "
                f"for {len(ids)} records. Pass re_embed_missing=True to fall back to "
                "the OpenAI API."
            )
        logger.warning(
            "Embeddings missing or incomplete in ChromaDB — falling back to OpenAI re-embedding. "
            "This will incur API costs proportional to corpus size."
        )
        embeddings = _re_embed(docs)

    records: list[dict[str, Any]] = []
    skipped = 0

    for chunk_id, text, meta, emb in zip(ids, docs, metas, embeddings):
        if not text or not text.strip():
            skipped += 1
            continue
        if emb is None or len(emb) == 0:
            logger.warning("Skipping chunk_id=%r — no embedding.", chunk_id)
            skipped += 1
            continue

        records.append(
            {
                "chunk_id": chunk_id,
                "source": (meta or {}).get("source", "unknown"),
                "text": text,
                # Chroma stores embeddings as numpy arrays or lists; coerce to list[float]
                "embedding": [float(v) for v in emb],
                "metadata": meta or {},
            }
        )

    logger.info(
        "Prepared %d records for migration (%d skipped due to empty text/embedding).",
        len(records),
        skipped,
    )
    return records


async def migrate(
    collection_name: str,
    batch_size: int,
    dry_run: bool,
) -> None:
    if not settings.database_url:
        logger.error(
            "DATABASE_URL is not set. Add it to .env:\n"
            "  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname"
        )
        sys.exit(1)

    collection = _open_collection(collection_name)
    records = fetch_all_records(collection)

    if not records:
        logger.info("Nothing to migrate. Exiting.")
        return

    if dry_run:
        logger.info(
            "[DRY-RUN] Would upsert %d records in batches of %d. No DB writes.",
            len(records),
            batch_size,
        )
        for i, r in enumerate(records[:5]):
            logger.info(
                "  [%d] chunk_id=%r  source=%r  text_len=%d  emb_dim=%d",
                i,
                r["chunk_id"],
                r["source"],
                len(r["text"]),
                len(r["embedding"]),
            )
        if len(records) > 5:
            logger.info("  … and %d more.", len(records) - 5)
        return

    logger.info(
        "Upserting %d records into PostgreSQL in batches of %d…",
        len(records),
        batch_size,
    )
    async with get_session() as session:
        total = await bulk_upsert_embeddings(session, records, batch_size=batch_size)

    logger.info("Migration complete. %d records upserted.", total)

    await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate ChromaDB embeddings to PostgreSQL pgvector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--collection",
        default="geo_risk_docs",
        help="ChromaDB collection name (default: geo_risk_docs)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        metavar="N",
        help="Records per upsert batch (default: 64)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing to the database",
    )
    args = parser.parse_args()

    asyncio.run(
        migrate(
            collection_name=args.collection,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
