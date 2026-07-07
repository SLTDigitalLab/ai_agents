import logging
from dotenv import load_dotenv

# --- 0. Load Environment Variables First ---
# This ensures Langfuse (and other services) pick up the .env credentials immediately
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat, orders, enterprise, admin_dashboard, feedback, finance, kb_retrieval, contact
from services.ingestion import router as ingestion_router

app = FastAPI(
    title="Ask SLT API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
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

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}