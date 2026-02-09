import os
from pathlib import Path
from typing import List

from georisk_agent.rag.vector_store import get_collection


def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> List[str]:
    """
    Split text into overlapping chunks.
    Simple, deterministic chunking for a first RAG version.
    """
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap

    return chunks


def ingest_directory(dir_path: str):
    collection = get_collection()
    base_path = Path(dir_path)

    for file_path in base_path.glob("*.txt"):
        text = file_path.read_text(encoding="utf-8")
        chunks = chunk_text(text)

        ids = [f"{file_path.stem}_{i}" for i in range(len(chunks))]
        metadatas = [{"source": file_path.name} for _ in chunks]

        collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids,
        )

        print(f"Ingested {len(chunks)} chunks from {file_path.name}")


if __name__ == "__main__":
    ingest_directory("data/documents")
