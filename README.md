# GeoRisk Intelligence Engine

A multi-agent geopolitical risk intelligence system designed to generate evidence-backed market insights from global geopolitical developments.

This is not a chatbot — it is a structured decision-support system that combines agentic reasoning, retrieval grounding, and market-aware analysis.

---

## Overview

GeoRisk Intelligence Engine analyzes geopolitical scenarios and translates them into actionable market intelligence.

The system orchestrates multiple specialized agents to:

- Break down complex geopolitical questions  
- Retrieve intelligence from a curated knowledge base  
- Generate evidence-grounded analysis  
- Identify market impacts and downside risks  
- Produce structured, institutional-style reports  

Built to demonstrate production-oriented AI engineering practices rather than experimental prompting.

---

## System Architecture

```mermaid
flowchart TD

A[User Query] --> B[Planner Agent]

B --> C[RAG Retriever]
C --> D[Evidence Store]

D --> E[Analysis Agent]

E --> F[Structured Intelligence Report]
