"""
Aegis — Web Scraping API

Exposes the Playwright-based scraper (app.core.scraper) as a small set of
endpoints: check/install the headless browser (downloaded on demand into
AEGIS_DATA_DIR, never bundled into the app), and scrape a URL for the
current conversation — the frontend "+" menu's "Scrape a Web Page" action
just POSTs a URL here.

Deliberately ephemeral: only files the user explicitly uploads go into the
vector DB (see app.api.documents). A scrape is shown directly in the chat
transcript as a message and NOT persisted as a UserDocument or embedded into
Qdrant — no Files panel entry, no later RAG retrieval. If a follow-up
question needs the page again, it gets scraped again.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import anyio

from app.core.connection_manager import manager as ws_manager
from app.core.scraper import scrape_url, is_chromium_installed, install_chromium
from app.db.database import SessionLocal
from app.db.crud import add_chat_message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scrape", tags=["Web Scraping"])

# In-memory install state — a single desktop-app process, so no DB needed.
_install_state = {"status": "not_installed"}


@router.get("/status")
def get_status():
    if is_chromium_installed():
        _install_state["status"] = "ready"
    return {
        "status": _install_state["status"],
        "component": _install_state.get("component"),
        "percent": _install_state.get("percent"),
    }


async def _run_install():
    _install_state["status"] = "installing"
    await ws_manager.broadcast_json({"type": "browser_install_progress", "status": "installing"})

    def on_progress(component: str, percent: float) -> None:
        # Fires from install_chromium's own worker thread (this whole
        # function runs via anyio.to_thread.run_sync below) — updated here
        # too (not just broadcast) so a client that only loads/polls
        # /status after missing the websocket message still sees real
        # progress, not just "installing" with no number.
        _install_state["component"] = component
        _install_state["percent"] = percent
        anyio.from_thread.run(
            ws_manager.broadcast_json,
            {"type": "browser_install_progress", "status": "installing", "component": component, "percent": percent},
        )

    ok, err = await anyio.to_thread.run_sync(lambda: install_chromium(on_progress))
    # `playwright install chromium` also fetches its paired FFMPEG and
    # Headless Shell builds — extra components this app's scraper never
    # actually uses (is_chromium_installed only ever checks for the real
    # browser). A hiccup on either of those two (confirmed live: a disk-
    # space error partway through Headless Shell) makes the CLI report
    # overall failure even though the one thing that actually matters —
    # the real Chromium browser web_scrape drives — downloaded fine and is
    # fully usable. Don't block a working scraper on that.
    if not ok and is_chromium_installed():
        ok = True
    _install_state["status"] = "ready" if ok else "failed"
    _install_state.pop("component", None)
    _install_state.pop("percent", None)
    await ws_manager.broadcast_json({
        "type": "browser_install_progress",
        "status": _install_state["status"],
        "error": None if ok else err,
    })


@router.post("/install")
async def start_install(background_tasks: BackgroundTasks):
    if is_chromium_installed():
        _install_state["status"] = "ready"
        return {"status": "ready"}
    if _install_state["status"] == "installing":
        return {"status": "installing"}
    background_tasks.add_task(_run_install)
    return {"status": "installing"}


class ScrapeRequest(BaseModel):
    url: str
    conversation_id: str


_PREVIEW_CHARS = 4000


async def _run_scrape(url: str, conversation_id: str):
    await ws_manager.broadcast_json({
        "type": "document_progress",
        "content": f"Opening {url} in a headless browser...",
    })

    result = await scrape_url(url)

    if not result.success:
        await ws_manager.broadcast_json({
            "type": "document_progress",
            "content": f"❌ Could not scrape {url}: {result.error}",
        })
        return

    preview = result.text[:_PREVIEW_CHARS]
    truncated_note = (
        f"\n\n_(showing the first {_PREVIEW_CHARS} characters — ask a follow-up question "
        "if you need something further down the page)_"
        if len(result.text) > _PREVIEW_CHARS else ""
    )
    warning_note = f"\n\n_Note: {' '.join(result.warnings)}_" if result.warnings else ""
    message_content = f"**Scraped: {result.title or url}**\n{url}\n\n{preview}{truncated_note}{warning_note}"

    # Shown directly in the conversation as a real message — persisted the
    # same way any other chat message is (so it survives reload/session
    # switch), but deliberately NOT a UserDocument and NOT embedded into
    # Qdrant. See this module's docstring for why.
    db = SessionLocal()
    try:
        add_chat_message(db, conversation_id, "assistant", message_content)
    finally:
        db.close()

    # step_result is the same live-append event Agent Mode already uses to
    # show a tool's output as its own message without waiting for a full
    # turn to finish — reused here so this appears immediately, not just on
    # next reload.
    await ws_manager.broadcast_json({"type": "step_result", "content": message_content})


@router.post("")
async def scrape(req: ScrapeRequest, background_tasks: BackgroundTasks):
    if not is_chromium_installed():
        raise HTTPException(status_code=409, detail="Browser not installed yet. Call /api/scrape/install first.")

    url = req.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    background_tasks.add_task(_run_scrape, url, req.conversation_id)
    return {"status": "started"}
