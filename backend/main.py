"""
Aegis FastAPI Sidecar — Entry Point

Run directly:
    cd backend && uvicorn main:app --reload --port 8000

Or as a script:
    python main.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load the .env file from the project root
root_dir = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=root_dir / ".env")

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.websocket import router as ws_router
from app.api.auth import router as auth_router
from app.api.connectors import router as connectors_router
from app.api.oauth_routes import router as oauth_router   # generic OAuth for Slack, Notion, etc.
from app.api.memories import router as memories_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router

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
app.include_router(auth_router)          # /auth/google/login  + /auth/google/callback
app.include_router(oauth_router)         # /auth/{service}/login + /auth/{service}/callback
app.include_router(connectors_router)    # /api/connectors/*
app.include_router(memories_router)      # /api/memories/*
app.include_router(chat_router)          # /api/chat/*
app.include_router(documents_router)     # /api/documents/*

# ─── Startup: SQLite Initialization & OAuth Auto-Restore ──────────────────────

@app.on_event("startup")
async def on_startup():
    """Initialize SQLite database tables, seed default model, and auto-restore Google OAuth session."""
    import asyncio
    import logging
    from app.db.database import init_db, SessionLocal
    from app.db.crud import seed_default_model, get_active_google_credentials
    from app.mcp.registry import mcp_registry
    from app.core.scheduler import scheduler_daemon
    from app.api.websocket import watch_timeouts
    
    _logger = logging.getLogger("startup")
    _logger.info("Initializing SQLite Database...")
    init_db()

    # Start the Scheduler Daemon for background jobs
    scheduler_daemon.start()

    # Start the WebSocket session timeout watcher
    asyncio.create_task(watch_timeouts())

    with SessionLocal() as db:
        # Seed default local model in SQLite models table
        model_path = str(Path(__file__).resolve().parent / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf")
        seed_default_model(db, model_path)

        # Auto-restore saved Google OAuth credentials from SQLite
        credentials = get_active_google_credentials(db)
        if credentials:
            try:
                mcp_registry.connect_google_workspace(
                    credentials_json_str=credentials.to_json(),
                    db=db
                )
                _logger.info("Auto-restored active Google Workspace MCP server from SQLite!")
            except Exception as e:
                _logger.error(f"Failed to auto-restore Google Workspace MCP server: {e}")
        else:
            _logger.info("No saved Google OAuth credentials found in SQLite.")

@app.on_event("shutdown")
def on_shutdown():
    from app.core.scheduler import scheduler_daemon
    scheduler_daemon.stop()

# ─── Direct Execution ────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",   # Bind to loopback only — never expose externally
        port=8000,
        reload=False,        # Disable reload when run as a spawned binary
        log_level="info",
    )
