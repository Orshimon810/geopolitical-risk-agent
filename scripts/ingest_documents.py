import asyncio
import hashlib
import logging
from pathlib import Path
from typing import List

from openai import OpenAI
from pypdf import PdfReader

from georisk_agent.app.config import settings
from georisk_agent.db.client import get_session
from georisk_agent.db.dal import bulk_upsert_embeddings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_openai = OpenAI(api_key=settings.openai_api_key)


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> List[str]:
    text = text.replace("\x00", " ").replace("\t", " ").strip()

    chunks = []
    start = 0
    max_len = 1000

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()

        if chunk and len(chunk) > 50:
            if len(chunk) > max_len:
                chunk = chunk[:max_len]
            chunks.append(chunk)

        start = end - overlap

    return chunks


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            pages.append(t)
    return "\n".join(pages)


def _embed_batch(texts: List[str]) -> List[List[float]]:
    response = _openai.embeddings.create(
        input=texts,
        model="text-embedding-3-small",
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def ingest_directory(dir_path: str) -> None:
    base_path = Path(dir_path)
    records = []

    for file_path in sorted(base_path.iterdir()):
        if file_path.suffix.lower() == ".txt":
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        elif file_path.suffix.lower() == ".pdf":
            logger.info("Reading PDF: %s", file_path.name)
            text = read_pdf(file_path)
        else:
            continue

        if not text.strip():
            logger.info("Skipped empty file: %s", file_path.name)
            continue

        chunks = chunk_text(text)
        if not chunks:
            logger.info("No chunks created for: %s", file_path.name)
            continue

        logger.info("Embedding %d chunks from %s ...", len(chunks), file_path.name)

        EMBED_BATCH = 64
        embeddings: List[List[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            embeddings.extend(_embed_batch(chunks[i : i + EMBED_BATCH]))

        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = hashlib.sha256(chunk.encode()).hexdigest()
            records.append({
                "chunk_id": chunk_id,
                "source": file_path.name,
                "text": chunk,
                "embedding": embedding,
                "metadata": {"source": file_path.name},
            })

        logger.info("Prepared %d chunks from %s", len(chunks), file_path.name)

    if not records:
        logger.info("No documents found in %s", dir_path)
        return

    async with get_session() as session:
        total = await bulk_upsert_embeddings(session, records)
        await session.commit()

    logger.info("Ingested %d chunks total into pgvector.", total)


if __name__ == "__main__":
    asyncio.run(ingest_directory("data/documents"))
