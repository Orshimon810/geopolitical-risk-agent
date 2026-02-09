from typing import List, Dict, Any

from georisk_agent.rag.vector_store import get_collection


def retrieve(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Retrieve top-k relevant chunks for a query from the Chroma vector store.

    Returns a list of dicts:
      {
        "text": <chunk text>,
        "source": <metadata source>,
      }
    """
    collection = get_collection()

    res = collection.query(
        query_texts=[query],
        n_results=k,
        include=["documents", "metadatas"],
    )

    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]

    out = []
    for text, meta in zip(docs, metas):
        out.append(
            {
                "text": text,
                "source": (meta or {}).get("source", "unknown"),
            }
        )

    return out
