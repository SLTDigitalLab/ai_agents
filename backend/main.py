import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback
from services.ingestion import router as ingestion_router

app = FastAPI(title="Ask SLT API")

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

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}