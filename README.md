# Workmate AI & Enterprise AI Assistant Solutions

This repository brings together SLT Digital Lab’s Workmate AI and enterprise AI assistant solutions, built on a shared frontend and backend.

- **Workmate AI:** An internal workplace assistant with a supervisor and 10 specialist subagents: HR, Finance, Admin, IT, CIA, Network, Legal, Marketing, Enterprise Business, and Consumer Business.
- **Enterprise AI assistant solutions:** Separate assistants for LifeStore, Enterprise, Rainbow Pages, AI Expo, MintCRM, and Embryo, tailored to their respective products, services, and audiences.

See the [supervisor routing configuration](backend/domain/config/supervisor_routing.py) for Workmate AI’s specialists and the [frontend agent configuration](frontend/src/config/agents.js) for assistant definitions. Ask Enterprise is a separate solution from Workmate AI’s Enterprise Business subagent.

## What’s included

- Document search (RAG), streamed chat, source citations and evidence previews, saved conversations, and feedback.
- An admin dashboard for analytics, conversation review, and knowledge-base ingestion from files, websites, OneDrive, and SharePoint.
- Microsoft Entra ID sign-in, HR API integration, Enterprise lead capture through Bitrix24, and LifeStore product search and orders.
- Embeddable agent pages, an optional Ollama HR demo, and tracing integrations.

## How to run

### 1. Get the project

Install Git and Docker with Docker Compose, then run:

```bash
git clone https://github.com/SLTDigitalLab/ai_agents.git
cd ai_agents
```

### 2. Configure the environment

Create `.env` in the project root using your team’s development configuration. It is ignored by Git; there is currently no checked-in environment template. Compose passes its values to both application containers.

The essentials are:

| Configuration | What to set |
| --- | --- |
| Models | `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSIONS`, with the matching provider credentials. Main model defaults use Gemini. |
| Routing and guardrails | These default to OpenAI independently of the main model, so supply `OPENAI_API_KEY` or configure their `ROUTING_EMBEDDING_*` and `GUARDRAIL_*` settings. |
| Frontend API | `VITE_API_URL=http://localhost:8000` for local development. |
| Microsoft sign-in | `VITE_MSAL_CLIENT_ID`, `VITE_MSAL_AUTHORITY`, `MS_CLIENT_ID`, and `MS_TENANT_ID`. Register `http://localhost:3000/auth/callback` as a redirect URI for the frontend app. |
| Admin access | `VITE_ADMIN_EMAILS` and `ADMIN_AGENT_MAP` for dashboard visibility and ingestion permissions. |

Compose supplies `POSTGRES_URL` and `QDRANT_URL` automatically. See [backend settings](backend/core/config.py), [backend authentication](backend/core/auth.py), and [frontend authentication](frontend/src/authConfig.js) for configuration details. Only put public frontend configuration in `VITE_*` variables.

Configure integrations only when needed: Microsoft Graph credentials for ingestion, `BITRIX24_WEBHOOK_URL` for leads, `MAIL_*` for orders, and `SMTP_*` plus `CONTACT_RECEIVER_EMAIL` for the contact form. Neo4j, Ollama, remote KB access, and LifeStore setup are covered in [operations](docs/operations.md).

### 3. Start and open the app

Run from the project root:

```bash
docker compose up --build -d
docker compose ps
```

| Service | Local address |
| --- | --- |
| Frontend | http://localhost:3000 |
| API documentation | http://localhost:8000/api/docs |
| Qdrant dashboard | http://localhost:6335/dashboard |
| PostgreSQL | `localhost:5433` |

Sign in to access employee assistants. An authorized admin can populate the knowledge bases at `/admin/ingestion`; a fresh database does not include company documents. Ingestion and retrieval must use matching embedding models and dimensions.

For logs and shutdown:

```bash
docker compose logs --tail 100 backend
docker compose down
```

Development database data persists under `data/postgres` and `data/qdrant`.

### Run the app outside Docker

Use Python 3.11 and Node.js 22 to match the Dockerfiles. Start the databases first:

```bash
docker compose up -d db_postgres db_qdrant
```

Set these host addresses in the root `.env`:

```dotenv
POSTGRES_URL=postgresql://slt:slt123@localhost:5433/slt_db
QDRANT_URL=http://localhost:6335
```

In one terminal, start the backend:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

On macOS/Linux, activate with `source venv/bin/activate` instead. Document parsing and OCR also need the system dependencies listed in [backend/Dockerfile](backend/Dockerfile), including Poppler and Tesseract; Docker installs these for you.

In a second terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Both apps read the root `.env`; Vite can additionally load `.env.development.local` overrides. The frontend remains on port 3000. Restart the relevant app after changing environment values.

## Project map

| Location | Purpose |
| --- | --- |
| `frontend/src/` | React 19 + Vite UI, authentication, chat, forms, and admin pages |
| `backend/main.py`, `backend/routers/` | FastAPI entry point and HTTP endpoints |
| `backend/domain/` | LangGraph supervisor, agent archetypes, prompts, routing, and tools |
| `backend/core/` | Configuration, model factories, authentication, and PostgreSQL checkpointing |
| `backend/services/` | Ingestion, catalog handling, and business integrations |
| `mcp_lifestore/` | LifeStore MCP product tools, loaded by the backend’s LifeStore chat route |
| `backend/scripts/`, `scripts/` | Graph rebuild and scheduled knowledge-base refresh |
| `docker-compose*.yml`, `nginx/` | Container and reverse-proxy configuration |

Most chat uses `POST /api/v1/chat`: guardrails → agent graph → retrieval/API/form tools → streamed response. PostgreSQL stores conversation state and feedback; Qdrant stores document vectors. The LifeStore UI uses `POST /api/v1/lifestore/mcp-chat`, which returns JSON through a separate product-tool flow; the LangGraph LifeStore path also supports Qdrant + Neo4j retrieval.

Use the running [API documentation](http://localhost:8000/api/docs) for request schemas and the full endpoint list, including chat history, feedback, admin tools, orders, leads, contact, and API-key-protected KB retrieval.

## Deployment and maintenance

### Local voice language detection and fillers

The Gemini Workmate voice proxy can identify English, Sinhala, and Tamil from
microphone PCM using the local `TriLinSL/lid-ecapa-voxlingua107-si-ta-en`
model. Install `backend/requirements-voice-lid.txt` in **the same Python
environment that starts Uvicorn**. Provision the model's `hyperparams.yaml`,
`embedding_model.ckpt`, `classifier.ckpt`, and `label_encoder.txt` under
`backend/.audio_lid_cache/` before startup. This ignored directory contains
model weights only; user audio is held in bounded memory and is not saved.
The model warms in the background when a Gemini voice session starts so a
first-turn Tamil or Sinhala audio check can finish within its bounded window.
The model's distribution is Apache-2.0; consult its model card when deploying.

Workmate lookup fillers target approximately 2.5 seconds, then 10 and 16
seconds, and continue at wider intervals while the task runs. The scheduler
requests speech one second ahead of each target so Gemini can begin generating
audio. A bounded audio language check can correct an English transcript; it
never holds the Workmate lookup or filler timer indefinitely. For Gemini Live
calls, the existing Alnilam session speaks deterministic filler phrases while
the Workmate tool runs. Filler PCM streams through the same browser audio queue
and Luma avatar lip-sync as final answers. This consumes additional Gemini
output during long lookups, but opens no second provider connection. Test
actual audio output, language switching, and interruption in the deployment
browser.

See [Operations](docs/operations.md) for production startup, Nginx ports, optional services, collection naming, monthly refresh, and troubleshooting. Frontend build and lint commands are `npm run build` and `npm run lint` from `frontend/`.

Developed by SLT Digital Lab.
