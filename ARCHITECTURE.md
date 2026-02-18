# SLTMobitel AI Agent System - Architecture Spec

## High-Level Goal
A unified multi-agent system where a central "Supervisor" (Intent Identifier) routes user queries to the correct tool (RAG, SQL, or Forms).

## Tech Stack
- **Frontend:** React (Vite) + Tailwind CSS
- **Backend:** Python FastAPI
- **Orchestration:** LangGraph (Supervisor Architecture)
- **Database:** PostgreSQL (History/Auth) + Qdrant (Vector Store)
- **Containerization:** Docker Compose (3 Services: Frontend, Backend, Database)

## Key Components (Refer to Supervisor's Diagram)
1. **Container 1 (Frontend):** - 6 Tabs: Ask HR, Ask Finance, Ask Admin, Ask Process, Ask Enterprise, Ask Lifestore.
   - Entry point: `POST /api/v1/chat`.
2. **Container 2 (Backend):** - **Admin Module:** `WebLoader` and `FileLoader` to ingest PDFs/URLs -> Embeddings -> Qdrant.
   - **Domain Module:** `IntentIdentifier` (Router) -> Decides between `FormTool`, `RAGTool`, or `APITool`.
3. **Container 3 (Data):** - Qdrant (Vectors).
   - Postgres (Relational).

## Coding Rules
- Use modular routing in FastAPI.
- Use Pydantic models for all data validation.
- Docstrings for every function.