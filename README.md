# Ask SLT - SLTMobitel AI Assistants

**Ask SLT** is a suite of AI-powered enterprise assistants designed for SLTMobitel employees. It provides 6 specialized AI agents for different departments, each capable of knowledge-base search, live API calls, or generative UI forms depending on its archetype.

## Architecture

The project is a full-stack application orchestrated with Docker Compose.

| Service | Tech | Port |
|---------|------|------|
| **Frontend** | React 19, Vite, TailwindCSS, Framer Motion | 3000 |
| **Backend** | FastAPI, LangChain, LangGraph | 8000 |
| **PostgreSQL** | pgvector (ankane/pgvector) | 5433 |
| **Qdrant** | Vector database | 6333 |

### Request Flow

```
User Message (React)
  -> POST /api/v1/chat (FastAPI)
  -> Guardrail classification (parallel, gpt-4.1-nano)
  -> LangGraph agent graph (archetype-specific)
  -> Tools: Qdrant RAG / SQL API / Form state
  -> StreamingResponse (token-by-token)
  -> Frontend renders with source citations & feedback buttons
```

### Agents

| Agent | ID | Archetype | Capabilities |
|-------|----|-----------|--------------|
| Ask HR | `hr` | KB + API | RAG search + live ERP API (e.g., leave balance) |
| Ask Finance | `finance` | KB Only | RAG search over finance documents |
| Ask Admin | `admin` | KB Only | RAG search over admin documents |
| Ask Process | `process` | KB Only | RAG search over process documents |
| Ask Enterprise | `enterprise` | KB + Form | RAG + generative UI lead capture form (Bitrix24 CRM) |
| Ask Lifestore | `lifestore` | KB + Form | RAG + generative UI order form (email notification) |

### Three Agent Archetypes (`backend/domain/archetypes/`)

1. **KB Only** (`kb_agent.py`) - LLM decides to search the knowledge base or answer directly
2. **KB + API** (`kb_api_agent.py`) - LLM supervisor chooses between RAG and live API calls
3. **KB + Form** (`kb_form_agent.py`) - LLM triggers frontend forms via special tokens (`[RENDER_*_FORM]`)

## Features

- **Multi-Agent Architecture** - 6 dedicated agents with domain-specific knowledge bases and tools
- **Streaming Responses** - Token-by-token streaming from LangGraph to the frontend
- **RAG Pipeline** - Document ingestion (PDF, DOCX, PPTX, XLSX, URLs, OneDrive) into per-agent Qdrant collections
- **Guardrails** - Parallel intent/sentiment classification using a lightweight model to filter off-topic or sensitive queries
- **Generative UI Forms** - Enterprise and Lifestore agents emit tokens that render interactive forms in the frontend
- **Feedback System** - Thumbs-up/down ratings on bot responses, stored in PostgreSQL
- **Admin Dashboard** - Session analytics, conversation browser, feedback panel, and document ingestion UI
- **Source Citations** - Bot responses display source document references as clickable badges
- **Persistent Chat History** - Per-agent PostgreSQL schemas via LangGraph checkpointing; users can resume conversations
- **Authentication** - Azure AD / Microsoft Entra ID via MSAL
- **CRM Integration** - Enterprise leads pushed to Bitrix24 via webhook
- **Order Notifications** - Lifestore orders sent via Gmail SMTP (FastAPI-Mail)
- **Observability** - Optional LangSmith tracing integration

## API Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/api/v1/chat` | POST | Send a message to an agent (streaming response) |
| `/api/v1/chat/{agent_id}/{thread_id}` | GET | Retrieve chat history for a thread |
| `/api/v1/feedback` | POST | Submit or toggle feedback rating |
| `/api/v1/feedback/{agent}/{thread}` | GET | Get feedback for a conversation |
| `/api/v1/admin/dashboard/stats` | GET | Session statistics |
| `/api/v1/admin/dashboard/sessions` | GET | Paginated session list with search |
| `/api/v1/admin/dashboard/feedback` | GET | Feedback analytics |
| `/api/v1/admin/ingest-url` | POST | Ingest a website URL into an agent's knowledge base |
| `/api/v1/admin/ingest-onedrive` | POST | Ingest files from a OneDrive folder |
| `/api/v1/enterprise/lead` | POST | Submit an enterprise lead to Bitrix24 CRM |
| `/api/v1/orders/submit` | POST | Submit a Lifestore order (sends email) |

---

## Prerequisites

1. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Docker and Docker Compose)
2. **Git**

## How to Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/SLTDigitalLab/ai_agents.git
cd ai_agents
```

### 2. Configure Environment Variables

Create a `.env` file in the root directory. Docker Compose mounts it to both frontend and backend containers.

```env
# LLM
LLM_PROVIDER=openai          # or gemini
LLM_MODEL=gpt-4o
OPENAI_API_KEY=your_key
GOOGLE_API_KEY=your_key       # if using Gemini

# Embeddings
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072

# Guardrails
GUARDRAIL_PROVIDER=openai
GUARDRAIL_MODEL=gpt-4.1-nano

# Databases
POSTGRES_URL=postgresql://postgres:postgres@db_postgres:5432/askslt
QDRANT_URL=http://db_qdrant:6333

# Frontend
VITE_API_URL=http://localhost:8000
VITE_MSAL_CLIENT_ID=your_azure_client_id
VITE_MSAL_AUTHORITY=https://login.microsoftonline.com/your_tenant_id
VITE_ADMIN_EMAILS=admin1@slt.com.lk,admin2@slt.com.lk

# Microsoft Graph (OneDrive ingestion)
MS_CLIENT_ID=your_client_id
MS_CLIENT_SECRET=your_client_secret
MS_TENANT_ID=your_tenant_id

# Integrations
BITRIX24_WEBHOOK_URL=your_bitrix_webhook
MAIL_USERNAME=your_gmail
MAIL_PASSWORD=your_app_password

# Observability (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key
```

### 3. Start the Application

```bash
docker-compose up --build -d
```

### 4. Access the Application

| Service | URL |
|---------|-----|
| Frontend | [http://localhost:3000](http://localhost:3000) |
| API Docs (Swagger) | [http://localhost:8000/docs](http://localhost:8000/docs) |
| Qdrant Dashboard | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) |

### 5. Stopping the Application

```bash
docker-compose down          # Keep database data
docker-compose down -v       # Wipe database volumes
```

## Development (without Docker)

You still need the databases running:

```bash
docker-compose up -d db_postgres db_qdrant
```

**Backend:**
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

## Project Structure

```
├── backend/
│   ├── core/               # Config, LLM factory, checkpointer
│   ├── domain/
│   │   ├── archetypes/     # KB-only, KB+API, KB+Form agent builders
│   │   ├── registry.py     # Agent ID -> archetype mapping
│   │   ├── guardrails.py   # Intent & sentiment classification
│   │   └── state.py        # LangGraph AgentState TypedDict
│   ├── routers/            # FastAPI route handlers
│   ├── schemas/            # Pydantic models
│   ├── services/           # Ingestion, tools, external integrations
│   └── main.py             # App entrypoint
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── ChatInterface.jsx
│       │   ├── admin/      # Dashboard, ChatBrowser, FeedbackPanel, IngestionPanel
│       │   └── forms/      # LifestoreForm, EnterpriseForm
│       ├── config/agents.js
│       └── authConfig.js
├── docker-compose.yml
└── .env
```

---

*Developed by SLT Digital Lab*
