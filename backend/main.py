"""
Aegis FastAPI Sidecar — Entry Point

Run directly:
    cd backend && uvicorn main:app --reload --port 8000

Or as a script:
    python main.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load the .env file from the project root (Dev only — .env is not bundled in packaged builds)
if not getattr(sys, 'frozen', False):
    root_dir = Path(__file__).resolve().parent.parent
    load_dotenv(dotenv_path=root_dir / ".env")

# huggingface_hub reads these once, at import time, so they must be set
# before anything (fastembed, sentence_transformers, transformers) first
# imports it — which happens lazily, well after this point, inside
# app/core/rag/processor.py's model getters. Pinned explicitly rather than
# left to the library's own default: this is what bounds each individual
# request huggingface_hub makes while downloading the embedding/reranker
# models on first use, so a network that silently drops packets (instead
# of refusing the connection) can't stall a download indefinitely — see
# app/api/documents.py's _INGEST_TIMEOUT_SECONDS for the outer ceiling this
# composes with.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "10")

# mcp_google dispatches here, before any of the app's own routers/RAG
# stack/etc. get imported below — google_mcp_server.py is careful to keep
# stdout clean for its JSON-RPC handshake (see its own module docstring:
# "All output except MCP messages goes to stderr"), but that guarantee is
# only as good as everything ELSE this process happens to import. A
# frozen build re-invokes this exact executable as its MCP subprocess
# (see mcp/registry.py's connect_google_service), so if the heavy import
# chain below (uvicorn, every app.api.* router, the embedding/reranker/
# RAG stack) ever prints so much as a blank line at import time on some
# platform, it lands on stdout ahead of the real handshake response and
# breaks the client's very first json.loads() with a cryptic "Expecting
# value: line 1 column 1 (char 0)" that has nothing to do with the actual
# cause. Dispatching before those imports even happen closes that off
# entirely, and starts the subprocess faster besides.
if len(sys.argv) > 1 and sys.argv[1] == "mcp_google":
    from app.mcp.servers.google_mcp_server import run_server
    run_server(sys.argv[2:])
    sys.exit(0)

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.websocket import router as ws_router
from app.api.auth import router as auth_router
from app.api.connectors import router as connectors_router, custom_mcp_router
from app.api.oauth_routes import router as oauth_router   # generic OAuth for Slack, Notion, etc.
from app.api.memories import router as memories_router
from app.core.feature_flags import CONNECTORS_ENABLED
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.models_hub import router as models_hub_router
from app.api.context_config import router as context_config_router
from app.api.analytics import router as analytics_router
from app.api.scraping import router as scraping_router
from app.api.voice import router as voice_router
from app.api.marketplace import router as marketplace_router
from app.api.marketplace_databases import router as marketplace_databases_router
from app.api.marketplace_embeddings import router as marketplace_embeddings_router
from app.api.marketplace_rerankers import router as marketplace_rerankers_router
from app.api.conversation_capabilities import router as conversation_capabilities_router
from app.api.export import router as export_router
from app.api.workflows import router as workflows_router
from app.api.aegis_db import router as aegis_db_router
from app.api.account_auth import router as account_auth_router

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
#
# Deliberately no "null" origin: that's what ANY locally-opened HTML file
# (a downloaded file, an email attachment, a sandboxed iframe) reports as
# its Origin — allowing it here meant any such content on the machine could
# make cross-origin requests to this API. The packaged app's production
# load already has its own real origin ("app://-", registered with
# standard:true in electron/main.ts), so "null" was never actually needed
# for that legitimate case.
#
# Also no allow_credentials: this app is local-first and open source, with
# no account/login system of its own — nothing in this codebase's own
# frontend sends `credentials: 'include'` to this API, so there's no reason
# to allow credentialed cross-origin requests at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Next.js dev server
        "http://localhost",       # Electron production (file-based loads)
        "app://-",                # Electron production custom protocol
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(health_router, prefix="/api")
app.include_router(ws_router)
# Custom MCP servers (GET/POST/DELETE /api/connectors/*, minus /catalog*) carry
# no OAuth dependency, so they're always on — see custom_mcp_router's own
# docstring in app/api/connectors.py for why this is split from the block below.
app.include_router(custom_mcp_router)
# The catalog router is also always on now — list_catalog/connect_from_catalog
# in app/api/connectors.py filter out auth_type=="oauth" entries themselves
# while CONNECTORS_ENABLED is off, so only the ones that need zero OAuth
# broker (api_key/path/connection_string/none) are ever reachable here.
app.include_router(connectors_router)        # /api/connectors/catalog*
if CONNECTORS_ENABLED:
    app.include_router(auth_router)          # /auth/google/login  + /auth/google/callback
    app.include_router(oauth_router)         # /auth/{service}/login + /auth/{service}/callback
app.include_router(memories_router)      # /api/memories/*
app.include_router(chat_router)          # /api/chat/*
app.include_router(documents_router)     # /api/documents/*
app.include_router(models_hub_router)    # /api/hub/*
app.include_router(context_config_router) # /api/context-config
app.include_router(analytics_router)     # /api/analytics
app.include_router(scraping_router)      # /api/scrape/*
app.include_router(voice_router)         # /api/voice/*
app.include_router(marketplace_router)   # /api/marketplace/*
app.include_router(marketplace_databases_router)  # /api/marketplace/databases/*
app.include_router(marketplace_embeddings_router)  # /api/marketplace/embeddings/*
app.include_router(marketplace_rerankers_router)  # /api/marketplace/rerankers/*
app.include_router(conversation_capabilities_router)  # /api/conversations/{id}/capabilities
app.include_router(export_router)        # /api/export
app.include_router(workflows_router)     # /api/workflows/*
app.include_router(aegis_db_router)      # /api/aegis-db/*
app.include_router(account_auth_router)  # /api/account/*

# ─── Startup: SQLite Initialization & OAuth Auto-Restore ──────────────────────

# ── System Readiness State (polled by the Splash Screen) ─────────────────────
_system_status = {
    "sqlite": False,
    "qdrant": False,
    "embedding_models": False,
    "downloaded_models": [],
}

@app.get("/api/status")
async def get_system_status():
    """Returns the readiness state of all backend subsystems for the Splash Screen."""
    return _system_status


@app.get("/api/feature-flags")
async def get_feature_flags():
    """Single source of truth the frontend reads instead of hardcoding its own copy of these flags."""
    return {"connectors_enabled": CONNECTORS_ENABLED}


@app.on_event("startup")
async def on_startup():
    """Initialize SQLite, Vector DB, and auto-restore OAuth sessions."""
    import logging
    import threading
    from app.db.database import init_db, SessionLocal
    from app.db.crud import get_active_google_credentials, reconcile_model_registry, reconcile_stuck_documents
    from app.mcp.registry import mcp_registry
    from app.core.scheduler import scheduler_daemon
    from app.core.rag.processor import init_qdrant
    from app.db.models import ModelRegistry
    
    _logger = logging.getLogger("startup")
    _logger.info("Initializing Databases...")
    init_db()
    _system_status["sqlite"] = True
    init_qdrant()
    _system_status["qdrant"] = True

    from app.core.workflows.seed import seed_default_pipeline, ensure_builtin_vector_store, ensure_builtin_aegis_database
    with SessionLocal() as db:
        ensure_builtin_vector_store(db)
        ensure_builtin_aegis_database(db)
        seed_default_pipeline(db)

    # Remove any "downloaded" model rows whose file no longer exists on disk —
    # otherwise the UI can report a model as downloaded/active that isn't
    # really there (e.g. a stale seed row, or a file removed outside the app).
    with SessionLocal() as db:
        removed = reconcile_model_registry(db)
        if removed:
            _logger.warning(f"Reconciled model registry: removed {removed} orphaned row(s).")

    # Same idea for documents stuck 'processing' from a prior process
    # lifetime — see reconcile_stuck_documents's docstring.
    with SessionLocal() as db:
        fixed = reconcile_stuck_documents(db)
        if fixed:
            _logger.warning(f"Reconciled documents: marked {fixed} orphaned in-progress row(s) as failed.")

    # Same idea for documents stuck 'processing' from a prior process
    # lifetime — see reconcile_stuck_documents's docstring.
    with SessionLocal() as db:
        fixed = reconcile_stuck_documents(db)
        if fixed:
            _logger.warning(f"Reconciled documents: marked {fixed} orphaned in-progress row(s) as failed.")

    # Remove any "downloaded" model rows whose file no longer exists on disk —
    # otherwise the UI can report a model as downloaded/active that isn't
    # really there (e.g. a stale seed row, or a file removed outside the app).
    with SessionLocal() as db:
        removed = reconcile_model_registry(db)
        if removed:
            _logger.warning(f"Reconciled model registry: removed {removed} orphaned row(s).")

    # Refresh downloaded models list in status
    with SessionLocal() as db:
        downloaded = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").all()
        _system_status["downloaded_models"] = [m.display_name for m in downloaded]

    # Start the Scheduler Daemon for background jobs — still runs any
    # pre-existing ScheduledJob rows from before Agent Mode's removal (see
    # app.core.scheduler); nothing creates new ones anymore now that
    # schedule_plan's websocket handler is gone, but old jobs keep working.
    scheduler_daemon.start()

    # Reap browser_* tool sessions (app.core.browser_session) idle for too
    # long, so an agent that finishes browsing — or a user who just closes
    # the tab — doesn't leave a headless Chromium process running forever.
    from app.core.browser_session import start_reaper
    start_reaper()

    # Reap expired agent-triggered exports (app.api.export's in-memory store)
    # so a long-running backend doesn't accumulate exported files forever.
    from app.api.export import start_export_reaper
    start_export_reaper()

    # Reap browser_* tool sessions (app.core.browser_session) idle for too
    # long, so an agent that finishes browsing — or a user who just closes
    # the tab — doesn't leave a headless Chromium process running forever.
    from app.core.browser_session import start_reaper
    start_reaper()

    # Reap expired agent-triggered exports (app.api.export's in-memory store)
    # so a long-running backend doesn't accumulate exported files forever.
    from app.api.export import start_export_reaper
    start_export_reaper()

    # Eagerly preload embedding models in a background thread
    def _preload_embedding_models():
        try:
            _logger.info("Preloading embedding models in background...")
            from app.core.rag.processor import get_dense_model, get_sparse_model, get_reranker
            get_dense_model()
            get_sparse_model()
            get_reranker()
            _system_status["embedding_models"] = True
            _logger.info("Embedding models preloaded successfully.")
        except Exception as e:
            _logger.error(f"Failed to preload embedding models: {e}")
            _system_status["embedding_models"] = True  # Non-fatal: mark done so splash doesn't block
            return

        # One-time re-ingestion of every existing document under the
        # current embedding model — a no-op after the first successful run
        # (see migrate_documents_to_current_embedding's own marker-file
        # gate), but required once whenever the embedding model changes
        # (different output dimension = a new, empty Qdrant collection —
        # nothing uploaded before the change is searchable again until
        # this runs). Chained after the preload above rather than its own
        # thread so it never races the same models still being loaded.
        try:
            from app.core.rag.processor import migrate_documents_to_current_embedding
            migrate_documents_to_current_embedding()
        except Exception as e:
            _logger.error(f"Embedding migration pass failed: {e}")

    threading.Thread(target=_preload_embedding_models, daemon=True).start()

    # Eagerly load the active LLM in the background so it's already resident in
    # RAM by the time the user opens chat, instead of loading it lazily on the
    # first message (which is what caused the noticeable lag on first send).
    # Submitted to the same single-worker llm_executor a real chat request would
    # use, so there's no race between this and an actual chat request trying to
    # load the same model concurrently.
    def _preload_active_llm():
        try:
            from app.db.models import ModelRegistry
            with SessionLocal() as db:
                active = db.query(ModelRegistry).filter(
                    ModelRegistry.status == "downloaded",
                    ModelRegistry.is_active == True
                ).first()
                if not active:
                    active = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").first()
                if not active:
                    _logger.info("No downloaded model to preload.")
                    return
                model_name = active.name

            _logger.info(f"Preloading active LLM '{model_name}' in background...")
            from app.core.agents.chat import get_llm_manager
            get_llm_manager().get_model(model_name)
            _logger.info(f"LLM '{model_name}' preloaded successfully.")
        except Exception as e:
            _logger.error(f"Failed to preload active LLM: {e}")

    from app.core.agents.chat import llm_executor
    llm_executor.submit(_preload_active_llm)

    # Eagerly load the active LLM in the background so it's already resident in
    # RAM by the time the user opens chat, instead of loading it lazily on the
    # first message (which is what caused the noticeable lag on first send).
    # Submitted to the same single-worker llm_executor a real chat request would
    # use, so there's no race between this and an actual chat request trying to
    # load the same model concurrently.
    def _preload_active_llm():
        try:
            from app.db.models import ModelRegistry
            with SessionLocal() as db:
                active = db.query(ModelRegistry).filter(
                    ModelRegistry.status == "downloaded",
                    ModelRegistry.is_active == True
                ).first()
                if not active:
                    active = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").first()
                if not active:
                    _logger.info("No downloaded model to preload.")
                    return
                model_name = active.name

            _logger.info(f"Preloading active LLM '{model_name}' in background...")
            from app.core.agents.chat import get_llm_manager
            get_llm_manager().get_model(model_name)
            _logger.info(f"LLM '{model_name}' preloaded successfully.")
        except Exception as e:
            _logger.error(f"Failed to preload active LLM: {e}")

    from app.core.agents.chat import llm_executor
    llm_executor.submit(_preload_active_llm)

    # The OAuth/catalog connectors are paused (see
    # app.core.feature_flags.CONNECTORS_ENABLED) — restoring THOSE saved
    # servers stays behind the flag so the app comes up with none of them
    # active, consistent with the OAuth-catalog UI/API being hidden. A
    # user-added CUSTOM MCP server (command/args/env the user supplied
    # directly, no OAuth involved) has nothing to do with that pause —
    # see app/api/connectors.py's custom_mcp_router, mounted
    # unconditionally — so it always auto-restores below, flag or not.
    # The saved rows themselves are always untouched either way, so
    # flipping the flag back on restores catalog/OAuth auto-restore
    # exactly as it was.
    from app.db.crud import get_all_connected_servers
    from app.mcp.registry import reconnect_from_saved_config
    import json as _startup_json

    with SessionLocal() as db:
        if CONNECTORS_ENABLED:
            # Auto-restore saved Google OAuth credentials from SQLite
            for service_name in ["google_mail", "google_drive"]:
                credentials = get_active_google_credentials(db, service_name)
                if credentials:
                    try:
                        mcp_registry.connect_google_service(
                            service_name=service_name,
                            credentials_json_str=credentials.to_json(),
                            db=db
                        )
                        _logger.info(f"Auto-restored active {service_name} MCP server from SQLite!")
                    except Exception as e:
                        _logger.error(f"Failed to auto-restore {service_name} MCP server: {e}")

        connected_servers = get_all_connected_servers(db)
        for server in connected_servers:
            if server.name in ["google_mail", "google_drive", "google_workspace"]:
                continue  # already handled above (when enabled) or deprecated

            if not server.config_json:
                _logger.warning(f"Server '{server.name}' is marked connected but has no config_json. Cannot auto-restore.")
                continue

            # catalog/oauth servers only restore when the flag is on; a
            # custom server restores either way.
            try:
                config_type = _startup_json.loads(server.config_json).get("type")
            except Exception:
                config_type = None
            if config_type in ("catalog", "oauth") and not CONNECTORS_ENABLED:
                continue

            try:
                reconnect_from_saved_config(db, server)
                _logger.info(f"Auto-restored MCP server '{server.name}' from SQLite!")
            except Exception as e:
                _logger.error(f"Failed to auto-restore MCP server '{server.name}': {e}")

@app.on_event("shutdown")
def on_shutdown():
    from app.core.scheduler import scheduler_daemon
    scheduler_daemon.stop()

    # Close every open browser_* tool session so no headless Chromium
    # process outlives the backend (each session's own reaper thread would
    # get there eventually, but not until IDLE_TIMEOUT_SECONDS after this
    # process is already gone).
    from app.core.browser_session import close_all_sessions
    close_all_sessions()

    # Close every open browser_* tool session so no headless Chromium
    # process outlives the backend (each session's own reaper thread would
    # get there eventually, but not until IDLE_TIMEOUT_SECONDS after this
    # process is already gone).
    from app.core.browser_session import close_all_sessions
    close_all_sessions()

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "selftest_llama":
        # CI-only entry point: proves the packaged binary can actually load and run
        # a GGUF model through llama-cpp-python's compiled native library. The
        # /api/health check alone can't catch a missing/broken native lib — it never
        # touches llama_cpp (see main.spec's collect_dynamic_libs('llama_cpp')).
        from llama_cpp import Llama
        llm = Llama(model_path=sys.argv[2], n_ctx=64, n_gpu_layers=0, verbose=False)
        llm.create_completion("Hello", max_tokens=4)
        print("SELFTEST_LLAMA_OK")
        sys.exit(0)

    if len(sys.argv) > 2 and sys.argv[1] == "selftest_scrape":
        # CI-only entry point: proves the packaged binary can actually drive a
        # real headless browser scrape end to end (Node driver spawn, page
        # render, extraction) — the same class of check as selftest_llama
        # above, for the same reason: a packaged native-subprocess dependency
        # that /api/health never touches, so a broken build would otherwise
        # only surface the first time a user tries to scrape a page.
        import asyncio
        from app.core.scraper import scrape_url
        r = asyncio.run(scrape_url(sys.argv[2]))
        print(f"SELFTEST_SCRAPE_RESULT success={r.success} title={r.title!r} warnings={r.warnings} error={r.error}")
        sys.exit(0 if r.success else 1)

    uvicorn.run(
        app,
        host="127.0.0.1",   # Bind to loopback only — never expose externally
        port=8000,
        reload=False,        # Disable reload when run as a spawned binary
        log_level="info",
    )
