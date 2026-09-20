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

# Every outbound HTTPS call this app makes (to aegisaistudio.online for the
# account/desktop-handoff/plan-sync endpoints) uses Python's own bundled CA
# list (certifi) by default, which only trusts publicly-issued certificates.
# A user behind corporate/endpoint-security TLS inspection (a VPN client, an
# antivirus product, a managed network appliance — anything that re-signs
# HTTPS traffic with its own locally-generated CA to scan it) has that CA
# trusted by their OS and browsers, but NOT by certifi's bundle, so a plain
# httpx call fails with a certificate-verify error even though the same
# machine's browser loads the exact same site fine. truststore makes every
# ssl.SSLContext Python creates from this point on defer to the OS's own
# native trust evaluation (Keychain on macOS, Cert Store on Windows) instead
# — the same trust decision the system's own browsers and tools make, so
# this app's own network calls succeed under the same conditions a browser
# already would. Injected here, before any other import has a chance to
# create an SSLContext of its own.
import truststore
truststore.inject_into_ssl()

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

# Makes any package installed via the Dependencies panel's free-text pip
# install box (app/core/optional_deps.py — see its own module docstring)
# importable again this run, before anything below could ever need it.
from app.core.optional_deps import ensure_on_path
ensure_on_path()

# Attaches the Dependencies panel's recent-errors log viewer as early as
# possible — see app/core/log_buffer.py's own module docstring — so it's
# already capturing warnings/errors from everything imported below.
from app.core.log_buffer import install as install_log_buffer
install_log_buffer()

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
from app.api.voice import router as voice_router
from app.api.marketplace import router as marketplace_router
from app.api.marketplace_databases import router as marketplace_databases_router
from app.api.marketplace_embeddings import router as marketplace_embeddings_router
from app.api.marketplace_rerankers import router as marketplace_rerankers_router
from app.api.marketplace_media import router as marketplace_media_router
from app.api.optional_deps import router as optional_deps_router
from app.api.conversation_capabilities import router as conversation_capabilities_router
from app.api.export import router as export_router
from app.api.workflows import router as workflows_router
from app.api.aegis_db import router as aegis_db_router
from app.api.account_auth import router as account_auth_router
from app.api.updates import router as updates_router

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
app.include_router(voice_router)         # /api/voice/*
app.include_router(marketplace_router)   # /api/marketplace/*
app.include_router(marketplace_databases_router)  # /api/marketplace/databases/*
app.include_router(marketplace_embeddings_router)  # /api/marketplace/embeddings/*
app.include_router(marketplace_rerankers_router)  # /api/marketplace/rerankers/*
app.include_router(marketplace_media_router)  # /api/marketplace/media-engines/*
app.include_router(optional_deps_router)  # /api/optional-deps/*
app.include_router(conversation_capabilities_router)  # /api/conversations/{id}/capabilities
app.include_router(export_router)        # /api/export
app.include_router(workflows_router)     # /api/workflows/*
app.include_router(aegis_db_router)      # /api/aegis-db/*
app.include_router(account_auth_router)  # /api/account/*
app.include_router(updates_router)       # /api/updates/*

# ─── Startup: SQLite Initialization & OAuth Auto-Restore ──────────────────────

# ── System Readiness State (polled by the Splash Screen) ─────────────────────
_system_status = {
    "sqlite": False,
    "qdrant": False,
    "embedding_models": False,
    # Sub-stage + percentage for the embedding preload (dense -> sparse ->
    # reranker) — the reranker stage is what actually takes most of the
    # time (a full `sentence_transformers` import pulls in transformers/
    # sklearn/pandas/datasets even though this app only ever does
    # inference — see the cold-start investigation this replaced a flat
    # boolean with), so the splash screen can show real movement instead
    # of a frozen spinner for ~10-15s straight.
    "embedding_stage": "pending",  # pending | dense | sparse | reranker | done | failed
    "embedding_progress": 0,       # 0-100
    "llm_ready": False,
    "llm_stage": "pending",        # pending | none | loading | done | failed
    "llm_progress": 0,             # 0-100
    "llm_model_name": None,
    "downloaded_models": [],
}

@app.get("/api/status")
async def get_system_status():
    """Returns the readiness state of all backend subsystems for the Splash Screen."""
    return _system_status


def _tick_progress(status_key: str, start: int, end: int, duration_s: float, stop: "threading.Event") -> None:
    """Advances _system_status[status_key] from start toward end over
    duration_s, easing out (fast at first, slower near the end) so it never
    actually reaches `end` on its own — the caller snaps the real final
    value once the thing it's estimating for has genuinely finished. Neither
    sentence_transformers' import machinery nor llama_cpp's model loader
    expose a real progress callback, so this time-based estimate is the
    honest alternative to a frozen spinner for an operation known to take
    several seconds to tens of seconds."""
    steps = 50
    interval = max(0.05, duration_s / steps)
    for i in range(steps):
        # Event.wait() (vs. sleep()+is_set()) closes the race where stop is
        # set mid-sleep: sleep()+check-before would still write one more
        # stale, lower value right after the caller's own final write,
        # visibly ticking the percentage backwards.
        if stop.wait(interval):
            return
        frac = 1 - (1 - (i + 1) / steps) ** 2
        _system_status[status_key] = int(start + (end - start) * frac)


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

    # Refresh downloaded models list in status
    with SessionLocal() as db:
        downloaded = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").all()
        _system_status["downloaded_models"] = [m.display_name for m in downloaded]

    # Start the Scheduler Daemon for background jobs — still runs any
    # pre-existing ScheduledJob rows from before Agent Mode's removal (see
    # app.core.scheduler); nothing creates new ones anymore now that
    # schedule_plan's websocket handler is gone, but old jobs keep working.
    scheduler_daemon.start()

    # Reap expired agent-triggered exports (app.api.export's in-memory store)
    # so a long-running backend doesn't accumulate exported files forever.
    from app.api.export import start_export_reaper
    start_export_reaper()

    # Embedding models (dense/sparse/reranker) are no longer eagerly
    # preloaded here — Aegis's own default pipeline is a bare
    # chat_trigger -> llm -> chat_reply now (see app.core.workflows.seed),
    # not the earlier auto-RAG tree that actually used them on every turn.
    # Downloading BAAI/bge-base-en-v1.5 + a sparse model + the
    # sentence-transformers-based reranker at every fresh install's first
    # boot was real, unwanted default-download weight for an app that, by
    # default, never touches any of them — and the reranker specifically
    # would now just fail every single startup anyway, since
    # sentence_transformers isn't bundled any more (see main.spec's
    # excludes list). get_dense_model/get_sparse_model/get_reranker
    # (app.core.rag.processor) are already lazy — first real use (a
    # user-built RAG workflow, or a document search) initializes them
    # then, same one-time cost just moved from every boot to first actual
    # need. Nothing left to wait on, so the splash screen's embedding
    # stage reports done immediately instead of ticking through a
    # multi-second preload that no longer happens.
    _system_status["embedding_stage"] = "done"
    _system_status["embedding_progress"] = 100
    _system_status["embedding_models"] = True

    # Document text-extraction/ingestion is back (Extract node, document
    # upload) — existing "ready" documents from before an embedding-model
    # upgrade still need re-ingesting under the new model (see
    # migrate_documents_to_current_embedding's own docstring). Its own
    # guards (a marker file, an early return when there are no "ready"
    # documents at all) make this a cheap no-op on a fresh install; it only
    # actually touches get_dense_model() when there's real migration work,
    # so it doesn't reintroduce the eager-download preload removed above.
    def _migrate_embeddings_if_needed():
        try:
            from app.core.rag.processor import migrate_documents_to_current_embedding
            migrate_documents_to_current_embedding()
        except Exception as e:
            _logger.error(f"Embedding migration pass failed: {e}")

    threading.Thread(target=_migrate_embeddings_if_needed, daemon=True).start()

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
                    _system_status["llm_stage"] = "none"
                    _system_status["llm_ready"] = True
                    return
                model_name = active.name
                model_path = active.file_path

            _system_status["llm_model_name"] = model_name
            _system_status["llm_stage"] = "loading"
            _logger.info(f"Preloading active LLM '{model_name}' in background...")

            # llama_cpp's Llama() constructor has no progress callback either —
            # estimate the load duration from the GGUF's on-disk size (~150MB/s
            # is a conservative floor for mmap + KV-cache init across SSD-class
            # disks) so the splash bar still moves at something like the real rate.
            try:
                size_gb = os.path.getsize(model_path) / (1024 ** 3) if model_path and os.path.exists(model_path) else 2.0
            except Exception:
                size_gb = 2.0
            est_seconds = max(4.0, size_gb / 0.15)

            stop_ticker = threading.Event()
            ticker = threading.Thread(
                target=_tick_progress, args=("llm_progress", 0, 95, est_seconds, stop_ticker), daemon=True
            )
            ticker.start()
            try:
                from app.core.agents.chat import get_llm_manager
                get_llm_manager().get_model(model_name)
            finally:
                stop_ticker.set()

            _system_status["llm_progress"] = 100
            _system_status["llm_stage"] = "done"
            _system_status["llm_ready"] = True
            _logger.info(f"LLM '{model_name}' preloaded successfully.")
        except Exception as e:
            _logger.error(f"Failed to preload active LLM: {e}")
            _system_status["llm_stage"] = "failed"
            _system_status["llm_ready"] = True

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

    uvicorn.run(
        app,
        host="127.0.0.1",   # Bind to loopback only — never expose externally
        port=8000,
        reload=False,        # Disable reload when run as a spawned binary
        log_level="info",
    )
