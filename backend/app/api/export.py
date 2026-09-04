"""
Aegis — Serves files the export_document agent tool generates (/api/export)

Export is agent-only now: the user asks in chat ("give me this as a PDF",
"convert that to docx") and the export_document tool (app/core/agents/chat.py's
_execute_export_document) converts the relevant content via
app/core/exporter.py, stores the bytes here under a short-lived ID, and
returns a download link that appears directly in the chat message. This
module just serves that link — there used to also be a POST /api/export for
a manual per-message "Export ▾" button (ExportMenu.tsx), removed along with
that button now that the tool covers the same need conversationally.
"""

import re
import threading
import time
import uuid
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/export", tags=["Export"])

# In-memory store for agent-triggered exports — deliberately not persisted
# (no DB row, no file on disk): these are one-off, ephemeral downloads
# generated on request, same "nothing saved beyond this turn" philosophy as
# web_scrape's results. Reaped after _EXPORT_TTL_SECONDS so a long-idle
# backend doesn't accumulate exported files in memory forever.
_EXPORT_TTL_SECONDS = 3600  # 1 hour
_exports: Dict[str, dict] = {}
_exports_lock = threading.Lock()

# The backend always runs on this fixed local port (see electron/main.ts's
# BACKEND_PORT and every OAuth redirect URI in app/auth/ — same convention
# reused here) — a download link embedded in a chat message has to be an
# absolute URL pointing back at the backend, not a path the frontend's own
# Next.js origin would try to resolve instead.
BACKEND_BASE_URL = "http://127.0.0.1:8000"


def store_export(data: bytes, content_type: str, filename: str) -> str:
    """Stores generated file bytes, returning an opaque ID for the download
    URL. Called by the export_document agent tool — see this module's
    docstring."""
    export_id = uuid.uuid4().hex
    with _exports_lock:
        _exports[export_id] = {
            "data": data,
            "content_type": content_type,
            "filename": filename,
            "created_at": time.time(),
        }
    return export_id


def _reap_expired_exports() -> None:
    while True:
        time.sleep(300)
        now = time.time()
        with _exports_lock:
            expired = [k for k, v in _exports.items() if now - v["created_at"] > _EXPORT_TTL_SECONDS]
            for k in expired:
                _exports.pop(k, None)


def start_export_reaper() -> None:
    """Call once at app startup (main.py's on_startup)."""
    threading.Thread(target=_reap_expired_exports, daemon=True).start()


def _safe_filename(title: str, fmt: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9-_ ]+", "", title).strip() or "aegis-export"
    base = re.sub(r"\s+", "-", base)[:60]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{base}-{stamp}.{fmt}"


@router.get("/download/{export_id}")
def download_export(export_id: str):
    """Serves a file the export_document agent tool generated — see
    store_export/this module's docstring. One export_id can be downloaded
    more than once (a user might click the chat link twice); it just
    expires after _EXPORT_TTL_SECONDS regardless."""
    with _exports_lock:
        entry = _exports.get(export_id)
    if not entry:
        raise HTTPException(status_code=404, detail="This download has expired or doesn't exist.")

    return Response(
        content=entry["data"],
        media_type=entry["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )
