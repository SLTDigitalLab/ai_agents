# Workmate AI — SLTMobitel AI Assistants

**Workmate AI** is a suite of AI-powered enterprise assistants designed for SLTMobitel employees. The platform is centered around a unified **Workmate AI** supervisor that intelligently routes queries to 8 specialist agents covering HR, Finance, Admin, IT, CIA, Process, Enterprise, and Lifestore — each with its own knowledge base, tools, and archetype.

## Architecture

Full-stack application orchestrated with Docker Compose.

| Service | Tech | Port (dev) |
|---------|------|-----------|
| **Frontend** | React 19, Vite, TailwindCSS, Framer Motion | 3000 |
| **Backend** | FastAPI, LangChain, LangGraph | 8000 |
| **PostgreSQL** | pgvector (ankane/pgvector) | 5433 |
| **Qdrant** | Vector database | 6333 |

### Request Flow

```
User Message (React)
  -> POST /api/v1/chat (FastAPI)
  -> Guardrail classification (intent + sentiment, gpt-4.1-nano)
  -> LangGraph agent graph (supervisor or archetype-specific)
     -> Supervisor: vector-similarity routing to specialist
     -> Specialist: Qdrant RAG / SQL API / Form state / SLM
  -> StreamingResponse (token-by-token)
  -> Frontend renders with source citations & feedback buttons
```

### Agents

| Agent | Route Key | Archetype | Capabilities |
|-------|-----------|-----------|--------------|
| **Workmate AI** | `supervisor` | Supervisor | Routes queries to any specialist; answers general/platform questions directly |
| Ask HR | `hr` | KB + API | RAG over HR docs + live ERP API (leave balance) |
| Ask Finance | `finance` | KB Only | RAG over finance documents |
| Ask Admin | `admin` | KB Only | RAG over admin documents |
| Ask IT | `it` | KB Only | RAG over IT support documents |
| Ask CIA | `cia` | KB Only | RAG over internal audit / compliance documents |
| Ask Process | `process` | KB Only | RAG over SOP / process documents |
| Ask Enterprise | `enterprise` | KB + Form | RAG + generative UI lead capture form (Bitrix24 CRM) |
| Ask Lifestore | `lifestore` | KB + Form | RAG + chat-driven cart & **PayHere checkout** (sandbox demo); legacy email order form as fallback |
| Ask HR SLM *(demo)* | `askhrslm` | KB + SLM | On-prem inference via Ollama (DeepSeek-R1 1.5B) |

### Ask LifeStore — chat commerce (cart + PayHere)

Customers can add products to a cart and pay, all inside the chat. Scoped to the
LifeStore agent only — no other agent is affected.

**Flow:** browse → `add_to_cart` → `begin_checkout` → PayHere onsite checkout →
webhook/reconcile confirms → ✅/❌ pushed into the chat.

```
React chat ─► /api/v1/chat ─► LifeStore agent (cart tools)
                                 ├─ cart + order snapshot (Postgres, schema lifestore_payments)
                                 └─ order_id ──► [RENDER_LIFESTORE_CHECKOUT:<id>]
React <LifestoreCheckout> ─► GET /api/v1/lifestore/checkout/{id} (amount + signed PayHere payload)
                          ─► PayHere sandbox (payhere.js) ─► pays
                          ◄─ POST /api/v1/lifestore/payhere/notify   (md5sig-verified, idempotent — SOURCE OF TRUTH)
                          ◄─ GET  /api/v1/lifestore/orders/{id}      (frontend polls for settled status)
```

**Correctness guarantees (customer-facing):**
- Prices/totals are resolved server-side from the live catalog
  (`services/lifestore_catalog.py`), never from the LLM. The model only carries
  an opaque `order_id`; the amount + payment link come from the backend.
- Payment is marked PAID only by the md5sig-verified webhook (or the
  server-to-server PayHere Retrieval API reconcile) — never by the browser
  redirect. Status transitions are idempotent, so webhook retries are safe.

**Setup:** add sandbox credentials to `.env` (`PAYHERE_MERCHANT_ID`,
`PAYHERE_MERCHANT_SECRET`; keep `PAYHERE_SANDBOX=true`). The `notify_url` must be
publicly reachable — for local testing set `PAYHERE_NOTIFY_URL` to a tunnel
(e.g. ngrok), or configure `PAYHERE_APP_ID`/`PAYHERE_APP_SECRET` so the frontend
can reconcile status via PayHere's Retrieval API. Disable the whole flow with
`LIFESTORE_PAYMENTS_ENABLED=false` (falls back to the email order form). Every
checkout card is labelled **Sandbox demo — no real money is charged**.

Key files: `backend/services/lifestore_{catalog,payments_store,payhere}.py`,
`backend/routers/lifestore_payments.py`,
`backend/domain/tools/lifestore_cart_tools.py`,
`frontend/src/components/forms/LifestoreCheckout.jsx`.

### Five Agent Archetypes (`backend/domain/archetypes/`)

1. **Supervisor** (`supervisor_agent.py`) — Vector-similarity routing with keyword boosts, follow-up stickiness, and multi-agent delegation. Answers general/help questions directly.
2. **KB Only** (`kb_agent.py`) — LLM decides to search the knowledge base or answer directly.
3. **KB + API** (`kb_api_agent.py`) — LLM supervisor chooses between RAG and live API calls.
4. **KB + Form** (`kb_form_agent.py`) — LLM triggers frontend forms via special tokens (`[RENDER_*_FORM]`).
5. **KB + SLM** (`kb_slm_agent.py`) — KB-only agent powered by an internal on-premises Ollama model.

Routing profiles and thresholds for the supervisor live in `backend/domain/config/supervisor_routing.py`.

---

## Features

- **Workmate AI Supervisor** — Single entry point that routes any workplace question to the right specialist using embedding-based similarity and keyword matching
- **Multi-Agent Architecture** — 8 specialist agents with domain-specific knowledge bases and tools
- **Streaming Responses** — Token-by-token streaming from LangGraph to the frontend
- **RAG Pipeline** — Document ingestion (PDF, DOCX, PPTX, XLSX, URLs, OneDrive) into per-agent Qdrant collections
- **Guardrails** — Intent + sentiment classification using a lightweight model to filter off-topic or sensitive queries
- **Generative UI Forms** — Enterprise and Lifestore agents emit tokens that render interactive forms in the frontend
- **Feedback System** — Thumbs-up/down ratings on bot responses, stored in PostgreSQL
- **Admin Dashboard** — Session analytics, conversation browser, feedback panel, and document ingestion UI
- **Source Citations** — Bot responses display source document references as clickable badges
- **Persistent Chat History** — Per-agent PostgreSQL schemas via LangGraph checkpointing; users can resume conversations
- **Authentication** — Azure AD / Microsoft Entra ID via MSAL
- **CRM Integration** — Enterprise leads pushed to Bitrix24 via webhook
- **Order Notifications** — Lifestore orders sent via Gmail SMTP (FastAPI-Mail)
- **On-Prem SLM** — Optional Ollama-backed agent (DeepSeek-R1 1.5B) for zero-external-API inference
- **Iframe Embed** — Lifestore and Enterprise agents can be embedded as iframes in external pages
- **Observability** — Optional LangSmith tracing integration

---

## API Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/api/v1/chat` | POST | Send a message to an agent (streaming response) |
| `/api/v1/chat/{agent_id}/{thread_id}` | GET | Retrieve chat history for a thread |
| `/api/v1/feedback` | POST | Submit or toggle feedback rating |
| `/api/v1/feedback` | DELETE | Remove a feedback rating |
| `/api/v1/feedback/{agent_id}/{thread_id}` | GET | Get feedback for a conversation |
| `/api/v1/admin/dashboard/stats` | GET | Session statistics |
| `/api/v1/admin/dashboard/sessions` | GET | Paginated session list with search |
| `/api/v1/admin/dashboard/sessions/{agent}/{session_id}` | GET | Full conversation for a session |
| `/api/v1/admin/dashboard/feedback` | GET | Feedback analytics |
| `/api/v1/admin/ingest-url` | POST | Ingest a website URL into an agent's knowledge base |
| `/api/v1/admin/ingest-onedrive` | POST | Ingest files from a OneDrive folder |
| `/api/v1/admin/ingestion-status` | GET | Poll the status of a running ingestion job |
| `/api/v1/admin/test-leave-balance` | POST | Test the HR leave balance API |
| `/api/v1/enterprise/lead` | POST | Submit an enterprise lead to Bitrix24 CRM |
| `/api/v1/enterprise/test-webhook` | POST | Test the Bitrix24 webhook connection |
| `/api/v1/orders/submit` | POST | Submit a Lifestore order (sends email) |
| `/api/v1/finance/retrieve` | POST | External Finance KB retrieval (voice assistant, API key required) |
| `/api/v1/kb/{agent_id}/retrieve` | POST | Generic per-agent KB retrieval (dev use, API key required) |

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
# ── LLM ──────────────────────────────────────────────────────────────────
LLM_PROVIDER=openai           # openai | gemini
LLM_MODEL=gpt-4o
OPENAI_API_KEY=your_key
GOOGLE_API_KEY=your_key       # if using Gemini

# ── Embeddings ────────────────────────────────────────────────────────────
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072

# ── Supervisor Routing Embeddings (cosine similarity over short text) ─────
ROUTING_EMBEDDING_PROVIDER=openai
ROUTING_EMBEDDING_MODEL=text-embedding-3-small
ROUTING_EMBEDDING_API_KEY=your_key   # falls back to OPENAI_API_KEY

# ── Guardrails ────────────────────────────────────────────────────────────
GUARDRAIL_PROVIDER=openai
GUARDRAIL_MODEL=gpt-4.1-nano
GUARDRAIL_API_KEY=your_key           # falls back to provider key

# ── Databases ─────────────────────────────────────────────────────────────
POSTGRES_URL=postgresql://slt:slt123@db_postgres:5432/slt_db
QDRANT_URL=http://db_qdrant:6333

# ── Frontend ──────────────────────────────────────────────────────────────
VITE_API_URL=http://localhost:8000
VITE_MSAL_CLIENT_ID=your_azure_client_id
VITE_MSAL_AUTHORITY=https://login.microsoftonline.com/your_tenant_id
VITE_ADMIN_EMAILS=admin1@slt.com.lk,admin2@slt.com.lk

# ── Microsoft Graph (OneDrive ingestion) ──────────────────────────────────
MS_CLIENT_ID=your_client_id
MS_CLIENT_SECRET=your_client_secret
MS_TENANT_ID=your_tenant_id

# ── Integrations ──────────────────────────────────────────────────────────
BITRIX24_WEBHOOK_URL=your_bitrix_webhook
MAIL_USERNAME=your_gmail
MAIL_PASSWORD=your_app_password
MAIL_FROM=your_gmail

# ── Admin access control ──────────────────────────────────────────────────
# JSON map: email -> list of agent_ids they may ingest into (["*"] = all agents)
ADMIN_AGENT_MAP={"hr.admin@slt.com.lk":["hr"],"super@slt.com.lk":["*"]}

# ── On-prem SLM (Ollama — optional, for Ask HR SLM demo only) ─────────────
SLM_BASE_URL=http://localhost:11434
SLM_EMBEDDING_BASE_URL=http://localhost:11434
SLM_MODEL=deepseek-r1:1.5b
SLM_EMBEDDING_MODEL=nomic-embed-text
SLM_EMBEDDING_DIMENSIONS=768

# ── External KB retrieval API keys (optional) ─────────────────────────────
VOICE_ASSISTANT_API_KEY=your_key      # Finance /retrieve endpoint
DEV_KB_API_KEY=your_key               # Generic /kb/{agent_id}/retrieve endpoint
KB_RETRIEVAL_ALLOWLIST=finance,hr     # Comma-separated agent_ids to expose

# Remote KB proxy (dev only — query prod vectors without local ingestion)
KB_REMOTE_URL=https://aiagents.sltdigitallab.lk
KB_REMOTE_API_KEY=your_key

# ── Graph DB (optional) ───────────────────────────────────────────────────
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

# ── Observability (optional) ──────────────────────────────────────────────
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
| API Docs (Swagger) | [http://localhost:8000/api/docs](http://localhost:8000/api/docs) |
| Qdrant Dashboard | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) |

### 5. Stopping the Application

```bash
docker-compose down          # Keep database data
docker-compose down -v       # Wipe database volumes
```

---

## Production Deployment

Use `docker-compose.prod.yml` for production. Key differences from the dev compose:
- Database ports are **not** exposed to the host — only reachable within the Docker network.
- Backend binds to `127.0.0.1:8100` and frontend to `127.0.0.1:3100`; Nginx proxies both.
- Named Docker volumes (`pgdata`, `qdrantdata`) replace bind-mounted `./data/` folders.

```bash
docker-compose -f docker-compose.prod.yml up --build -d
```

---

## Development (without Docker)

Start only the databases:

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

---

## Project Structure

```
├── backend/
│   ├── core/
│   │   ├── config.py           # Pydantic settings (env vars)
│   │   ├── llm.py              # LLM / embedding factory (cloud)
│   │   ├── llm_slm.py          # LLM / embedding factory (Ollama SLM)
│   │   └── checkpointer.py     # LangGraph PostgresSaver setup
│   ├── domain/
│   │   ├── archetypes/
│   │   │   ├── supervisor_agent.py   # Workmate AI — routes to specialists
│   │   │   ├── kb_agent.py           # Archetype 1: KB Only
│   │   │   ├── kb_api_agent.py       # Archetype 2: KB + API
│   │   │   ├── kb_form_agent.py      # Archetype 3: KB + Form
│   │   │   └── kb_slm_agent.py       # Archetype 4: KB + On-prem SLM
│   │   ├── config/
│   │   │   └── supervisor_routing.py # Routing profiles, thresholds, keywords
│   │   ├── tools/
│   │   │   ├── rag_tools.py          # Qdrant search (cloud embeddings)
│   │   │   ├── rag_tools_slm.py      # Qdrant search (Ollama embeddings)
│   │   │   ├── api_tools.py          # SQL / external API calls (HR ERP)
│   │   │   └── neo4j_tools.py        # Graph DB queries (optional)
│   │   ├── registry.py         # agent_id → archetype builder mapping
│   │   ├── guardrails.py       # Intent & sentiment classification
│   │   └── state.py            # LangGraph AgentState TypedDict
│   ├── routers/                # FastAPI route handlers
│   ├── schemas/                # Pydantic models
│   ├── services/               # Ingestion, ingestion status, external integrations
│   └── main.py                 # App entrypoint
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── ChatInterface.jsx
│       │   ├── admin/
│       │   │   ├── AdminDashboard.jsx
│       │   │   ├── AdminRoute.jsx
│       │   │   ├── ChatBrowser.jsx
│       │   │   ├── FeedbackPanel.jsx
│       │   │   ├── IngestionPanel.jsx
│       │   │   └── IframeChatPage.jsx  # Embeddable iframe view
│       │   └── forms/
│       │       ├── LifestoreForm.jsx
│       │       └── EnterpriseForm.jsx
│       ├── config/agents.js    # Agent metadata (title, colors, disclaimers)
│       └── authConfig.js       # MSAL / Azure AD config
├── nginx/                      # Nginx reverse-proxy config (prod)
├── docker-compose.yml          # Development compose
├── docker-compose.prod.yml     # Production compose
└── .env
```

---

*Developed by SLT Digital Lab — production deployment at [aiagents.sltdigitallab.lk](https://aiagents.sltdigitallab.lk)*
