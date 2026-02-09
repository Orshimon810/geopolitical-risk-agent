# 🧠 Architecture

```mermaid
flowchart TD
    A[User Query] --> B[Planner Agent]
    B --> C[RAG Retriever]
    C --> D[Vector Store]
    D --> E[Analysis Agent]
    E --> F[External Signals Agent]
    F --> G[Structured Output]
