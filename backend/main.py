import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback, finance, kb_retrieval
from services.ingestion import router as ingestion_router
from core.checkpointer import close_sync_pools, aclose_async_pools


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
app.include_router(enterprise.router)  # Enterprise lead → Bitrix24
app.include_router(admin_dashboard.router)  # Admin dashboard panel
app.include_router(feedback.router)  # Feedback (thumbs up/down)
app.include_router(finance.router)  # External Finance KB retrieval (voice assistant)
app.include_router(kb_retrieval.router)  # Generic per-agent KB retrieval (dev local → prod vectors)
app.include_router(ingestion_router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}