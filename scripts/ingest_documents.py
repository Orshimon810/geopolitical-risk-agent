import os
from pathlib import Path
from typing import List

from pypdf import PdfReader

from georisk_agent.rag.vector_store import get_collection


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> List[str]:
    """
    Robust chunking with hard limits and cleaning for embeddings safety.
    """
    text = (
        text.replace("\x00", " ")
        .replace("\u0000", " ")
        .replace("\t", " ")
        .strip()
    )

    chunks = []
    start = 0
    max_len = 1000  # hard safety cap for embeddings

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
    """
    Extract text from a PDF file using pypdf.
    """
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            pages.append(t)
    return "\n".join(pages)


def ingest_directory(dir_path: str):
    collection = get_collection()
    base_path = Path(dir_path)

    for file_path in base_path.iterdir():
        if file_path.suffix.lower() == ".txt":
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            source = file_path.name

        elif file_path.suffix.lower() == ".pdf":
            print(f"Reading PDF: {file_path.name}")
            text = read_pdf(file_path)
            source = file_path.name

        else:
            continue

        if not text.strip():
            print(f"Skipped empty file: {file_path.name}")
            continue

        chunks = chunk_text(text)
        if not chunks:
            print(f"No chunks created for: {file_path.name}")
            continue

        ids = [f"{file_path.stem}_{i}" for i in range(len(chunks))]
        metadatas = [{"source": source} for _ in chunks]

        BATCH_SIZE = 64

    for i in range(0, len(chunks), BATCH_SIZE):
        collection.add(
        documents=chunks[i:i + BATCH_SIZE],
        metadatas=metadatas[i:i + BATCH_SIZE],
        ids=ids[i:i + BATCH_SIZE],
        )

        print(f"Ingested {len(chunks)} chunks from {file_path.name}")


if __name__ == "__main__":
    ingest_directory("data/documents")
