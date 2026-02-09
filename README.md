# 🌍 Geopolitical Risk & Markets Agent

An **agentic AI system** for geopolitical risk analysis, combining  
**retrieval-augmented generation (RAG)**, **context-aware external macroeconomic signals**,  
and a lightweight **interactive UI**.

---

## 📌 Overview

This project implements an end-to-end **agentic pipeline** that:

- Decomposes complex geopolitical queries into structured research plans
- Grounds analysis in a curated document corpus (RAG)
- Enriches insights with **context-aware external macroeconomic signals**
- Produces structured, evidence-backed market impact assessments

The system is designed to resemble **internal research tools** used by risk, policy, and strategy teams.
The system is orchestrated using a **LangGraph-based agent state machine**.

---

## 🤖 Agents

### Planner Agent
- Breaks the user query into focused research sub-questions
- Defines analytical scope and relevance

### RAG Research Agent
- Retrieves relevant document chunks from a vector database
- Uses a curated corpus (e.g., IMF, World Bank, BIS reports)

### Analysis Agent
- Produces structured outputs:
  - `MARKET_IMPACTS`
  - `RISKS`
  - `CONFIDENCE`
- Enforces strict citation and formatting guardrails

### External Signals Agent
- Extracts relevant countries from the query context
- Fetches macroeconomic indicators (e.g., Trade % of GDP)
  via the **World Bank Public API**
- Adds contextual signals without influencing core reasoning

---

## 🔑 Configuration

This project uses **OpenAI-compatible LLMs** for planning and analysis.

To run the system locally, you must provide an API key via an environment variable.

### Required Environment Variables

```bash
OPENAI_API_KEY=your_api_key_here
```
---

## 🖥️ Interfaces

### Streamlit UI

Run an interactive end-to-end analysis:
```bash
streamlit run ui/app.py 
```
### CLI
```bash
python scripts/run_planner.py
```
---

## ⚙️ Tech Stack

- Python
- LangGraph (agent orchestration)
- LangChain
- Vector Database (Chroma)
- OpenAI-compatible LLMs
- World Bank Public API
- Streamlit

---

## 🔒 Data & Ethics

- Public data sources only
- No proprietary or sensitive data
- Analysis is **non-prescriptive** and **not investment advice**

---

## ⚠️ Disclaimer

This project is for educational and research purposes only  
and does not constitute financial or investment advice.



