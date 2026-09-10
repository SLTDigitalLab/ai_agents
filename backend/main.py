import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback, finance, kb_retrieval, voice, lifestore_mcp_chat, lifestore_payments
from routers.voice_agent import lifestore_realtime, realtime
from services.ingestion import router as ingestion_router
from core.config import evidence_storage_dir, settings

app = FastAPI(
    title="Ask SLT API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# --- Evidence image storage ---
# Cropped PDF image/table previews are rendered during ingestion and served
# as static files so the frontend can display them as "Relevant Evidence".
evidence_dir = evidence_storage_dir()
evidence_dir.mkdir(parents=True, exist_ok=True)

app.mount(
    settings.EVIDENCE_URL_PREFIX,
    StaticFiles(directory=str(evidence_dir)),
    name="evidence_images",
)

# --- 1. Add CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], # React URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. Register Routers ---
app.include_router(admin.router)
app.include_router(chat.router)  # Connect the new chat endpoint
app.include_router(orders.router)  # LifeStore order submissions
app.include_router(lifestore_payments.router)  # LifeStore cart + PayHere checkout
app.include_router(enterprise.router)  # Enterprise lead → Bitrix24
app.include_router(admin_dashboard.router)  # Admin dashboard panel
app.include_router(feedback.router)  # Feedback (thumbs up/down)
app.include_router(finance.router)  # External Finance KB retrieval (voice assistant)
app.include_router(kb_retrieval.router)  # Generic per-agent KB retrieval (dev local → prod vectors)
app.include_router(ingestion_router)
app.include_router(voice.router)  # Voice STT/TTS
app.include_router(realtime.router)  # Live Voice Agent — Realtime API (/api/v1/realtime/*)
app.include_router(lifestore_realtime.router)  # LifeStore Voice Agent — isolated realtime API
app.include_router(lifestore_mcp_chat.router)  # LifeStore MCP proxy (product search chat)

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}
