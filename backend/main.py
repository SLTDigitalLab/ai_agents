import logging
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

# --- 0. Load Environment Variables First ---
# This ensures Langfuse (and other services) pick up the .env credentials immediately
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback, finance, kb_retrieval, contact, lifestore_mcp_chat
from services.ingestion import router as ingestion_router
from core.config import settings
from core.checkpointer import close_sync_pools, aclose_async_pools
from routers.voice_agent import realtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage shared resources for the app's lifetime.

    Checkpointer connection pools are created lazily on first use (per agent)
    and live for the whole process; we close them cleanly on shutdown.
    """
    yield
    await aclose_async_pools()
    close_sync_pools()


app = FastAPI(
    title="Ask SLT API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
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
app.include_router(contact.router)  # Contact Us email form
app.include_router(lifestore_mcp_chat.router)  # Ask LifeStore MCP chat (/api/v1/lifestore/*)
app.include_router(realtime.router)


@app.post("/api/simli/session", tags=["Simli"])
async def simli_session(response: Response):
    """Issue a short-lived avatar token; Simli credentials stay on the server."""
    config = {
        name: (getattr(settings, name, None) or "").strip()
        for name in ("SIMLI_API_KEY", "SIMLI_FACE_ID")
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise HTTPException(503, "Set " + ", ".join(missing) + " in backend/.env.")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            upstream = await client.post(
                "https://api.simli.ai/compose/token",
                headers={"x-simli-api-key": config["SIMLI_API_KEY"]},
                json={
                    "faceId": config["SIMLI_FACE_ID"],
                    "handleSilence": True,
                    "maxSessionLength": 600,
                    "maxIdleTime": 600,
                },
            )
        upstream.raise_for_status()
        token = upstream.json().get("session_token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Invalid token")
    except httpx.TimeoutException:
        raise HTTPException(504, "Simli timed out. Please reconnect.") from None
    except (httpx.HTTPError, ValueError, AttributeError):
        raise HTTPException(
            502, "Unable to start the avatar. Check your Simli key, face ID and available minutes."
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return {"session_token": token}

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}
