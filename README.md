Built an agentic geopolitical risk analysis system using LangGraph and RAG.
The system decomposes complex geopolitical questions, retrieves evidence from a curated corpus via a vector database (Chroma), and produces evidence-grounded market impact analysis with explicit risk assessment and confidence estimation.


## System Architecture

```mermaid
flowchart TD

A[User Query] --> B[Planner Agent]

B --> C[RAG Retriever]
C --> D[Evidence Store]

D --> E[Analysis Agent]

E --> F[Signals Agent]

F --> G[Structured Intelligence Report]
