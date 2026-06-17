import logging
from dotenv import load_dotenv

# --- 0. Load Environment Variables First ---
# This ensures Langfuse (and other services) pick up the .env credentials immediately
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback, finance, kb_retrieval
from services.ingestion import router as ingestion_router
from core.config import settings

app = FastAPI(
    title="Ask SLT API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# --- Evidence image storage ---
# Cropped PDF image/table previews are rendered during ingestion and served
# as static files so the frontend can display them as "Relevant Evidence".
evidence_dir = Path(settings.EVIDENCE_STORAGE_DIR)
if not evidence_dir.is_absolute():
    evidence_dir = Path(__file__).resolve().parent / evidence_dir

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
app.include_router(chat.router)  # The Langfuse logic goes inside here!
app.include_router(orders.router)  
app.include_router(enterprise.router)  
app.include_router(admin_dashboard.router)  
app.include_router(feedback.router)  
app.include_router(finance.router)  
app.include_router(kb_retrieval.router)  
app.include_router(ingestion_router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}