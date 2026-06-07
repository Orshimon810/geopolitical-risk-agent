import threading

from chromadb import PersistentClient
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from georisk_agent.app.config import settings

_lock = threading.Lock()
_collection = None


def get_collection(name: str = "geo_risk_docs"):
    """
    Returns a persistent Chroma collection for storing and querying documents.

    Singleton — initialized once and shared across threads to avoid
    concurrent PersistentClient initialization races.
    """
    global _collection

    if _collection is not None:
        return _collection

    with _lock:
        if _collection is not None:
            return _collection

        if not settings.openai_api_key:
            raise ValueError(
                "Missing OPENAI_API_KEY. Create a .env file with OPENAI_API_KEY=... "
                "and restart your terminal."
            )

        client = PersistentClient(path=settings.chroma_dir)

        embedding_fn = OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name="text-embedding-3-small",
        )

        _collection = client.get_or_create_collection(
            name=name,
            embedding_function=embedding_fn,
        )

    return _collection
