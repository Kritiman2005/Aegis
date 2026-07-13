"""
Aegis FastAPI Sidecar — Entry Point

Run directly:
    cd backend && uvicorn main:app --reload --port 8000

Or as a script:
    python main.py
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.websocket import router as ws_router

# ─── App Factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Aegis Sidecar API",
    description="Local-first AI agent backend for the Aegis desktop platform.",
    version="0.1.0",
    docs_url="/docs",      # Swagger UI (disable in production if needed)
    redoc_url="/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
# Allow requests from Electron renderer and Next.js dev server.
# In production, restrict origins to file:// and http://localhost:3000.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Next.js dev server
        "http://localhost",       # Electron production (file-based loads)
        "null",                   # file:// origin appears as 'null' in browsers
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(health_router, prefix="/api")
app.include_router(ws_router)

# ─── Direct Execution ────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",   # Bind to loopback only — never expose externally
        port=8000,
        reload=False,        # Disable reload when run as a spawned binary
        log_level="info",
    )
