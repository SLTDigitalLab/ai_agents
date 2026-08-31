# Workmate AI — SLTMobitel AI Assistants

**Workmate AI** is a suite of AI-powered enterprise assistants designed for SLTMobitel employees. The platform is centered around a unified **Workmate AI** supervisor that intelligently routes queries to 8 specialist agents covering HR, Finance, Admin, IT, CIA, Process, Enterprise, and Lifestore — each with its own knowledge base, tools, and archetype.

## Architecture

Full-stack application orchestrated with Docker Compose.

| Service        | Tech                                       | Port (dev) |
| -------------- | ------------------------------------------ | ---------- |
| **Frontend**   | React 19, Vite, TailwindCSS, Framer Motion | 3000       |
| **Backend**    | FastAPI, LangChain, LangGraph              | 8000       |
| **PostgreSQL** | pgvector (ankane/pgvector)                 | 5433       |
| **Qdrant**     | Vector database                            | 6333       |
| **Neo4j**      | Graph database for LifeStore product facts | 7687       |

### Request Flow

```text
User Message (React)
  -> POST /api/v1/chat (FastAPI)
  -> Guardrail classification (intent + sentiment, gpt-4.1-nano)
  -> LangGraph agent graph (supervisor or archetype-specific)
     -> Supervisor: vector-similarity routing to specialist
     -> Specialist: Qdrant RAG / SQL API / Form state / SLM
     -> Ask Lifestore: hybrid retrieval using Qdrant + Neo4j
  -> StreamingResponse (token-by-token)
  -> Frontend renders with source citations & feedback buttons
```

### Agents

| Agent               | Route Key    | Archetype         | Capabilities                                                                  |
| ------------------- | ------------ | ----------------- | ----------------------------------------------------------------------------- |
| **Workmate AI**     | `supervisor` | Supervisor        | Routes queries to any specialist; answers general/platform questions directly |
| Ask HR              | `hr`         | KB + API          | RAG over HR docs + live ERP API (leave balance)                               |
| Ask Finance         | `finance`    | KB Only           | RAG over finance documents                                                    |
| Ask Admin           | `admin`      | KB Only           | RAG over admin documents                                                      |
| Ask IT              | `it`         | KB Only           | RAG over IT support documents                                                 |
| Ask CIA             | `cia`        | KB Only           | RAG over internal audit / compliance documents                                |
| Ask Process         | `process`    | KB Only           | RAG over SOP / process documents                                              |
| Ask Enterprise      | `enterprise` | KB + Form         | RAG + generative UI lead capture form (Bitrix24 CRM)                          |
| Ask Lifestore       | `lifestore`  | KB + Form + Graph | Hybrid Qdrant + Neo4j product retrieval, plus generative UI order form        |
| Ask HR SLM *(demo)* | `askhrslm`   | KB + SLM          | On-prem inference via Ollama (DeepSeek-R1 1.5B)                               |

### Five Agent Archetypes (`backend/domain/archetypes/`)

1. **Supervisor** (`supervisor_agent.py`) — Vector-similarity routing with keyword boosts, follow-up stickiness, and multi-agent delegation. Answers general/help questions directly.
2. **KB Only** (`kb_agent.py`) — LLM decides to search the knowledge base or answer directly.
3. **KB + API** (`kb_api_agent.py`) — LLM supervisor chooses between RAG and live API calls.
4. **KB + Form** (`kb_form_agent.py`) — LLM triggers frontend forms via special tokens (`[RENDER_*_FORM]`).
5. **KB + SLM** (`kb_slm_agent.py`) — KB-only agent powered by an internal on-premises Ollama model.

Routing profiles and thresholds for the supervisor live in `backend/domain/config/supervisor_routing.py`.

---

## Features

* **Workmate AI Supervisor** — Single entry point that routes any workplace question to the right specialist using embedding-based similarity and keyword matching
* **Multi-Agent Architecture** — 8 specialist agents with domain-specific knowledge bases and tools
* **Streaming Responses** — Token-by-token streaming from LangGraph to the frontend
* **RAG Pipeline** — Document ingestion (PDF, DOCX, PPTX, XLSX, URLs, OneDrive) into per-agent Qdrant collections
* **LifeStore Hybrid RAG** — Ask Lifestore uses Qdrant vector retrieval for product page descriptions and Neo4j graph retrieval for structured product facts
* **Neo4j Product Graph** — LifeStore product facts such as seller, stock status, price, brand, category, product type, URL, image URL, description, and specifications are stored and queried through Neo4j
* **Guardrails** — Intent + sentiment classification using a lightweight model to filter off-topic or sensitive queries
* **Generative UI Forms** — Enterprise and Lifestore agents emit tokens that render interactive forms in the frontend
* **Feedback System** — Thumbs-up/down ratings on bot responses, stored in PostgreSQL
* **Admin Dashboard** — Session analytics, conversation browser, feedback panel, and document ingestion UI
* **Source Citations** — Bot responses display source document references as clickable badges
* **Persistent Chat History** — Per-agent PostgreSQL schemas via LangGraph checkpointing; users can resume conversations
* **Authentication** — Azure AD / Microsoft Entra ID via MSAL
* **CRM Integration** — Enterprise leads pushed to Bitrix24 via webhook
* **Order Notifications** — Lifestore orders sent via Gmail SMTP (FastAPI-Mail)
* **On-Prem SLM** — Optional Ollama-backed agent (DeepSeek-R1 1.5B) for zero-external-API inference
* **Iframe Embed** — Lifestore and Enterprise agents can be embedded as iframes in external pages
* **Observability** — Optional LangSmith tracing integration

---

## API Routes

| Route                                                   | Method | Description                                                       |
| ------------------------------------------------------- | ------ | ----------------------------------------------------------------- |
| `/api/v1/chat`                                          | POST   | Send a message to an agent (streaming response)                   |
| `/api/v1/chat/{agent_id}/{thread_id}`                   | GET    | Retrieve chat history for a thread                                |
| `/api/v1/feedback`                                      | POST   | Submit or toggle feedback rating                                  |
| `/api/v1/feedback`                                      | DELETE | Remove a feedback rating                                          |
| `/api/v1/feedback/{agent_id}/{thread_id}`               | GET    | Get feedback for a conversation                                   |
| `/api/v1/admin/dashboard/stats`                         | GET    | Session statistics                                                |
| `/api/v1/admin/dashboard/sessions`                      | GET    | Paginated session list with search                                |
| `/api/v1/admin/dashboard/sessions/{agent}/{session_id}` | GET    | Full conversation for a session                                   |
| `/api/v1/admin/dashboard/feedback`                      | GET    | Feedback analytics                                                |
| `/api/v1/admin/ingest-url`                              | POST   | Ingest a website URL into an agent's knowledge base               |
| `/api/v1/admin/ingest-onedrive`                         | POST   | Ingest files from a OneDrive folder                               |
| `/api/v1/admin/ingestion-status`                        | GET    | Poll the status of a running ingestion job                        |
| `/api/v1/admin/test-leave-balance`                      | POST   | Test the HR leave balance API                                     |
| `/api/v1/enterprise/lead`                               | POST   | Submit an enterprise lead to Bitrix24 CRM                         |
| `/api/v1/enterprise/test-webhook`                       | POST   | Test the Bitrix24 webhook connection                              |
| `/api/v1/orders/submit`                                 | POST   | Submit a Lifestore order (sends email)                            |
| `/api/v1/finance/retrieve`                              | POST   | External Finance KB retrieval (voice assistant, API key required) |
| `/api/v1/kb/{agent_id}/retrieve`                        | POST   | Generic per-agent KB retrieval (dev use, API key required)        |

---

## Prerequisites

1. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Docker and Docker Compose)
2. **Git**

---

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
VITE_ABP_AGENT_URL=/abpagent/

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

# ── LifeStore / Enterprise KB Collections ─────────────────────────────────
LIFESTORE_QDRANT_COLLECTION=lifestore
LIFESTORE_QDRANT_DELETE_COLLECTION=lifestore_docs
LIFESTORE_QDRANT_SEARCH_COLLECTION=lifestore_docs

ENTERPRISE_QDRANT_COLLECTION=enterprise
ENTERPRISE_QDRANT_DELETE_COLLECTION=enterprise_docs

CLEAR_QDRANT_BEFORE_INGEST=true
DELETE_BASE_QDRANT_COLLECTION_TOO=true

# ── Graph DB / Neo4j ──────────────────────────────────────────────────────
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

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

# ── Observability (optional) ──────────────────────────────────────────────
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key
```

### 3. Start the Application

```bash
docker-compose up --build -d
```

### 4. Access the Application

| Service            | URL                             |
| ------------------ | ------------------------------- |
| Frontend           | http://localhost:3000           |
| API Docs (Swagger) | http://localhost:8000/api/docs  |
| Qdrant Dashboard   | http://localhost:6333/dashboard |
| Neo4j Browser      | http://localhost:7474           |

### 5. Stopping the Application

```bash
docker-compose down          # Keep database data
docker-compose down -v       # Wipe database volumes
```

---

## Production Deployment

Use `docker-compose.prod.yml` for production. Key differences from the dev compose:

* Database ports are not exposed to the host unless explicitly mapped.
* Backend is exposed through the internal production port and reverse-proxied by Nginx.
* Frontend is served through Nginx.
* Named Docker volumes replace bind-mounted local data folders.
* Monthly refresh automation runs inside the backend container.

```bash
docker-compose -f docker-compose.prod.yml up --build -d
```

### Current Production Notes

Current production project path:

```text
/opt/Ask_SLT
```

Current public site:

```text
https://aiagents.sltdigitallab.lk
```

Current Ask Lifestore iframe/page:

```text
https://aiagents.sltdigitallab.lk/asklifestore
```

Current production service mapping:

| Service    | Host Port | Container Port |
| ---------- | --------- | -------------- |
| Frontend   | 3000      | 3000           |
| Backend    | 8100      | 8000           |
| Qdrant     | 6335      | 6333           |
| PostgreSQL | 5433      | 5432           |

Production internal values used inside the backend container:

```env
ADMIN_BASE_URL=http://127.0.0.1:8000
QDRANT_URL=http://slt_qdrant:6333
```

Host-side test URLs:

```text
Backend API: http://127.0.0.1:8100/api/v1/admin/ingestion-status
Qdrant: http://127.0.0.1:6335
Frontend: http://127.0.0.1:3000
```

### Nginx Production Routing

Nginx proxies:

```text
/               -> frontend
/asklifestore   -> frontend
/api/...        -> backend
```

For the current production setup, the frontend should proxy to port `3000`, and backend API routes should proxy to port `8100`.

---

## Development Without Docker

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

```text
├── backend/
│   ├── core/
│   │   ├── config.py                 # Pydantic settings (env vars)
│   │   ├── llm.py                    # LLM / embedding factory (cloud)
│   │   ├── llm_slm.py                # LLM / embedding factory (Ollama SLM)
│   │   └── checkpointer.py           # LangGraph PostgresSaver setup
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
│   │   │   ├── rag_tools.py          # Qdrant + Neo4j hybrid search for LifeStore
│   │   │   ├── rag_tools_slm.py      # Qdrant search (Ollama embeddings)
│   │   │   ├── api_tools.py          # SQL / external API calls (HR ERP)
│   │   │   └── neo4j_tools.py        # Graph DB queries (optional)
│   │   ├── registry.py               # agent_id → archetype builder mapping
│   │   ├── guardrails.py             # Intent & sentiment classification
│   │   └── state.py                  # LangGraph AgentState TypedDict
│   ├── routers/                      # FastAPI route handlers
│   ├── schemas/                      # Pydantic models
│   ├── services/                     # Ingestion, ingestion status, external integrations
│   ├── scripts/
│   │   ├── monthly_kb_refresh.py     # Monthly Qdrant + Neo4j refresh orchestration
│   │   └── build_lifestore_graph.py  # Rebuilds LifeStore product graph in Neo4j
│   └── main.py                       # App entrypoint
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
│       ├── config/agents.js          # Agent metadata (title, colors, disclaimers)
│       └── authConfig.js             # MSAL / Azure AD config
├── nginx/                            # Nginx reverse-proxy config (prod)
├── scripts/
│   └── run_monthly_kb_refresh_prod.sh # Production cron wrapper
├── logs/
│   └── monthly_kb_refresh.log        # Production refresh log
├── docker-compose.yml                # Development compose
├── docker-compose.prod.yml           # Production compose
└── .env
```

---

## Advanced Production Operations

This section documents the production LifeStore hybrid retrieval setup, Neo4j graph database usage, and monthly knowledge base refresh automation.

---

## LifeStore Hybrid RAG and Neo4j Graph Database

Ask Lifestore uses a hybrid retrieval setup:

1. **Qdrant vector database**

   * Stores scraped LifeStore website/product page content.
   * Best for product descriptions, product page text, fuzzy matching, and general product details.
   * Uses hybrid retrieval with dense embeddings and BM25 sparse search.

2. **Neo4j graph database**

   * Stores structured LifeStore product facts.
   * Best for exact product facts such as product name, seller, price, stock status, brand, category, product type, URL, image URL, description, and specifications.
   * Helps the bot answer exact product questions more reliably.

### LifeStore Hybrid Retrieval Flow

```text
User asks LifeStore question
  -> search_knowledge_base tool
  -> Qdrant search over lifestore_docs
  -> Neo4j graph search over Product nodes
  -> Combined context is passed to the LLM
  -> LLM answers using both vector context and verified graph facts
```

### LifeStore Qdrant Collections

LifeStore ingestion uses the base collection name:

```env
LIFESTORE_QDRANT_COLLECTION=lifestore
```

The actual Qdrant collection used for retrieval is:

```env
LIFESTORE_QDRANT_SEARCH_COLLECTION=lifestore_docs
```

The delete/refresh target is also:

```env
LIFESTORE_QDRANT_DELETE_COLLECTION=lifestore_docs
```

This separation is important because the ingestion layer may internally create the real collection with the `_docs` suffix. Retrieval must search `lifestore_docs`, not only `lifestore`.

### Enterprise Qdrant Collections

Enterprise ingestion uses:

```env
ENTERPRISE_QDRANT_COLLECTION=enterprise
ENTERPRISE_QDRANT_DELETE_COLLECTION=enterprise_docs
```

### LifeStore Neo4j Product Graph

LifeStore graph data is rebuilt from scraped LifeStore product data using:

```text
backend/scripts/build_lifestore_graph.py
```

The graph stores product nodes and related structured fields such as:

```text
Product name
Product ID / SKU
Seller
Price
Stock status
Stock quantity
Brand
Category
Product type
Tags
URL
Image URL
Description
Specifications
Last seen timestamp
Missing-from-latest-scrape flag
```

Example product facts returned from Neo4j:

```text
Product: Prolink DH5201 Dual-band Wi-Fi Extender
Description: Tired of Wi-Fi “dead zones”? The DH-5201 Dual-band Wi-Fi AC1200 Extender delivers high-speed connection...
Brand: Prolink
Seller: SLT-MOBITEL
Category: Wi-Fi Devices
Product Type: wifi extender
Price: Rs. 9,255.00
Stock Status: in_stock
URL: https://lifestore.lk/product/prolink-dh5201-dual-band-wi-fi-extender
Image URL: https://lifestore.lk/sites/default/files/inline-images/970_90.jpg
Tags: wifi extender
```

### Important Neo4j Path Note

Inside the backend Docker container, the graph rebuild script path is:

```text
/app/scripts/build_lifestore_graph.py
```

On the host server, the project path is:

```text
/opt/Ask_SLT/backend/scripts/build_lifestore_graph.py
```

The monthly refresh script supports both host and Docker paths, so it can run correctly from the backend container.

---

## Monthly Knowledge Base Refresh Automation

Production has a monthly automation that refreshes both Qdrant and Neo4j.

The automation runs:

1. LifeStore Qdrant ingestion
2. Enterprise Qdrant ingestion
3. LifeStore Neo4j graph rebuild

### Monthly Refresh Script

Main refresh script:

```text
backend/scripts/monthly_kb_refresh.py
```

Production wrapper script:

```text
scripts/run_monthly_kb_refresh_prod.sh
```

Production log file:

```text
logs/monthly_kb_refresh.log
```

### Manual Run

From the production host:

```bash
cd /opt/Ask_SLT
docker compose exec backend python scripts/monthly_kb_refresh.py
```

For heredoc or piped Python commands, use `-T`:

```bash
cd /opt/Ask_SLT

docker compose exec -T backend python - <<'PY'
print("Backend container command test")
PY
```

### Production Wrapper Script

The wrapper script should exist at:

```text
/opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

It should be executable:

```bash
ls -l /opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

Expected permission pattern:

```text
-rwxr-xr-x
```

### Cron Schedule

The production cron job is installed under the root user:

```bash
crontab -l
```

Expected cron line:

```cron
0 2 1 * * /opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

This runs the refresh automatically at **2:00 AM on the 1st day of every month**.

### Cron Setup

To add or edit the cron job:

```bash
crontab -e
```

Add:

```cron
0 2 1 * * /opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

Then verify:

```bash
crontab -l
```

### Monthly Refresh Environment Variables

Use these values in production `.env`:

```env
RUN_QDRANT_INGESTIONS=true
RUN_NEO4J_GRAPH_REFRESH=true

LIFESTORE_QDRANT_COLLECTION=lifestore
LIFESTORE_QDRANT_DELETE_COLLECTION=lifestore_docs
LIFESTORE_QDRANT_SEARCH_COLLECTION=lifestore_docs

ENTERPRISE_QDRANT_COLLECTION=enterprise
ENTERPRISE_QDRANT_DELETE_COLLECTION=enterprise_docs

CLEAR_QDRANT_BEFORE_INGEST=true
DELETE_BASE_QDRANT_COLLECTION_TOO=true
```

### Production Internal URLs

When the monthly refresh runs inside the backend container, use container/network addresses:

```env
ADMIN_BASE_URL=http://127.0.0.1:8000
QDRANT_URL=http://slt_qdrant:6333
```

From the host server, these services are exposed through host ports:

```text
Backend host API: http://127.0.0.1:8100
Qdrant host API: http://127.0.0.1:6335
```

### Monthly Refresh Log Monitoring

Check the latest automation log:

```bash
tail -100 /opt/Ask_SLT/logs/monthly_kb_refresh.log
```

Search for success or failure messages:

```bash
grep -n "Production monthly KB refresh\|Monthly KB refresh completed\|failed\|Traceback\|ERROR" /opt/Ask_SLT/logs/monthly_kb_refresh.log | tail -50
```

Expected success messages:

```text
LifeStore ingestion finished successfully.
SLT Enterprise ingestion finished successfully.
LifeStore Neo4j graph rebuilt successfully.
Monthly KB refresh completed successfully.
Production monthly KB refresh finished at ...
```

---

## Production Health Checks

Check running containers:

```bash
cd /opt/Ask_SLT
docker compose ps
```

Check backend logs:

```bash
docker compose logs backend --tail 100
```

Check public frontend and API:

```bash
curl -I https://aiagents.sltdigitallab.lk/
curl -I https://aiagents.sltdigitallab.lk/asklifestore
curl -I https://aiagents.sltdigitallab.lk/api/v1/admin/ingestion-status
```

Check Qdrant collection counts:

```bash
cd /opt/Ask_SLT

docker compose exec -T backend python - <<'PY'
from qdrant_client import QdrantClient
from core.config import settings

client = QdrantClient(url=settings.QDRANT_URL)

for name in ["lifestore_docs", "enterprise_docs"]:
    try:
        print(name, client.count(name).count)
    except Exception as e:
        print(name, "ERROR", e)
PY
```

Check LifeStore graph retrieval:

```bash
cd /opt/Ask_SLT

docker compose exec -T backend python - <<'PY'
from domain.tools.rag_tools import _search_lifestore_graph

query = "Prolink DH5201 Dual-band Wi-Fi Extender functionalities features specs"

graph = _search_lifestore_graph(query=query, limit=1)
print(graph[:5000])
PY
```

Expected graph output should include product description, features, price, stock status, and URL.

---

## Common Production Troubleshooting

### 1. Old log shows `/backend/scripts/build_lifestore_graph.py`

If the log contains:

```text
RuntimeError: Neo4j graph script not found: /backend/scripts/build_lifestore_graph.py
```

that means an older version of the refresh script used the wrong Docker path.

Correct Docker path:

```text
/app/scripts/build_lifestore_graph.py
```

The log file is append-only, so old failed runs may still appear in `tail` or `grep`. Confirm the latest run instead:

```bash
tail -60 /opt/Ask_SLT/logs/monthly_kb_refresh.log
```

### 2. Qdrant collection `lifestore` does not exist

If backend logs show:

```text
Qdrant collection 'lifestore' does not exist for agent 'lifestore'
```

retrieval is using the wrong collection name.

Make sure this exists in `.env`:

```env
LIFESTORE_QDRANT_SEARCH_COLLECTION=lifestore_docs
```

Do not change `LIFESTORE_QDRANT_COLLECTION=lifestore` unless the ingestion implementation is changed.

### 3. LifeStore bot cannot verify product functionalities

If the bot can confirm price, stock, category, and product type but cannot answer product features, check whether Neo4j graph retrieval returns the product description:

```bash
cd /opt/Ask_SLT

docker compose exec -T backend python - <<'PY'
from domain.tools.rag_tools import _search_lifestore_graph

query = "Prolink DH5201 Dual-band Wi-Fi Extender functionalities features specs"

graph = _search_lifestore_graph(query=query, limit=1)
print(graph[:5000])
PY
```

Expected output should include:

```text
Description: Tired of Wi-Fi “dead zones”? ...
AC1200 Extender
up to 1,000 sq ft
smart LED indicator
Ethernet port
Access Point or Repeater mode
2.4GHz
5GHz
WPA-PSK/WPA2-PSK
```

If the description is missing, check `backend/domain/tools/rag_tools.py` and make sure both Neo4j `RETURN` blocks include:

```cypher
p.description AS description,
p.image_url AS image_url,
```

### 4. Harmless Docker Compose warning

This warning is harmless:

```text
WARN[0000] /opt/Ask_SLT/docker-compose.yml: the attribute `version` is obsolete
```

The app can still run. It only means the `version:` key in `docker-compose.yml` can be removed later to avoid confusion.

---

*Developed by SLT Digital Lab — production deployment at [aiagents.sltdigitallab.lk](https://aiagents.sltdigitallab.lk)*
