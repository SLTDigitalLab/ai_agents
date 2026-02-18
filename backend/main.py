from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import admin, chat  # Ensure chat is imported

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

@app.get("/")
def read_root():
    return {"message": "Welcome to Ask SLT API"}