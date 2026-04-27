from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from routers import admin, chat, orders, enterprise, admin_dashboard, feedback
from services.ingestion import router as ingestion_router
from jobs.semantic_cache_cleanup import cleanup_expired_semantic_cache

app = FastAPI(title="Ask SLT API")

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

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
app.include_router(ingestion_router)


@app.on_event("startup")
def _start_scheduler() -> None:
    """
    Hourly best-effort cleanup for expired semantic cache points in Qdrant.

    Important:
    - Request path safety does NOT depend on this job (semantic lookup enforces expiry).
    - Fail-open: if Qdrant/APScheduler is unavailable, the API still starts.
    """
    global _scheduler
    try:
        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(
            func=cleanup_expired_semantic_cache,
            trigger=IntervalTrigger(hours=1),
            id="semantic_cache_cleanup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.start()
        _scheduler = sched
        logger.info("APScheduler started (semantic cache cleanup hourly).")
    except Exception as exc:
        logger.warning("Scheduler startup failed; continuing without cleanup: %s", exc)
        _scheduler = None


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    global _scheduler
    try:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
    except Exception:
        pass
    finally:
        _scheduler = None


@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}