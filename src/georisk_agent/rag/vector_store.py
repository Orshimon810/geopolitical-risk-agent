from chromadb import PersistentClient
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from georisk_agent.app.config import settings


def get_collection(name: str = "geo_risk_docs"):
    """
    Returns a persistent Chroma collection for storing and querying documents.

    - Uses a local directory (settings.chroma_dir) for persistence
    - Uses OpenAI embeddings for vectorization
    """

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

    return client.get_or_create_collection(
        name=name,
        embedding_function=embedding_fn,
    )
