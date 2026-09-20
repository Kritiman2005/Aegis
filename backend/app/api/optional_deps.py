"""
Aegis — Optional Dependencies API (/api/optional-deps)

Backs the Dependencies panel — a free-text "pip install any package" box
plus a recent-errors log viewer, so a future dependency nothing in this
app anticipated doesn't require a whole new Aegis release just to try.
See app.core.optional_deps' own module docstring for the full story
(including why this replaced an earlier, narrower "make torch an opt-in
download" design). Same status-only progress shape as
marketplace_media.py's EasyOCR install (pip's own programmatic API gives
no byte-level progress callback to hook, unlike the huggingface_hub-backed
downloads elsewhere in this app).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core import optional_deps, log_buffer
from app.core.connection_manager import manager
from app.core.friendly_errors import humanize_exception

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/optional-deps", tags=["optional-deps"])

# In-memory current-status-by-package-name — see marketplace_media.py's
# _progress dict for why this rides on the existing polled list instead of
# relying solely on the websocket broadcast (this panel, like Marketplace,
# has no persistent websocket subscription of its own).
_progress: Dict[str, Dict[str, Any]] = {}


@router.get("")
def list_custom():
    packages = optional_deps.list_custom()
    for p in packages:
        p.update(_progress.get(p["name"], {}))
    return {"packages": packages}


@router.get("/bundled")
def list_bundled():
    """The app's own shipped dependencies (from requirements.txt) — read-only,
    shown alongside list_custom()'s user-installed ones so the Dependencies
    panel answers both "what does Aegis ship with" and "what did I add"."""
    return {"packages": optional_deps.list_bundled()}


class InstallRequest(BaseModel):
    spec: str


async def _install_task(spec: str) -> None:
    from app.core.optional_deps import slugify  # same name the install itself resolves to
    name = slugify(spec)

    async def broadcast(status: str, **kwargs) -> None:
        if status == "running":
            _progress[name] = {"status": status, **kwargs}
        else:
            _progress.pop(name, None)
        await manager.broadcast_json({"type": "optional_dep_install_progress", "name": name, "spec": spec, "status": status, **kwargs})

    await broadcast("running", message=f"Installing {spec}…")
    try:
        import anyio
        await anyio.to_thread.run_sync(optional_deps.install_custom_sync, spec)
        _progress.pop(name, None)
        await manager.broadcast_json({"type": "optional_dep_install_complete", "name": name, "spec": spec})
    except Exception as e:
        friendly = humanize_exception(e, context=f"installing '{spec}'")
        # INFO (not captured by log_buffer's WARNING+ filter) keeps the raw
        # exception in stdout for real debugging; the ERROR call below is
        # what the Dependencies panel's log viewer actually shows a user,
        # so it stays in plain language only.
        logger.info(f"Optional dependency install raw error for '{spec}': {e}")
        logger.error(friendly)
        _progress.pop(name, None)
        await manager.broadcast_json({"type": "optional_dep_install_failed", "name": name, "spec": spec, "message": friendly})


@router.post("/install")
def install_package(req: InstallRequest, background_tasks: BackgroundTasks):
    spec = req.spec.strip()
    if not spec:
        raise HTTPException(status_code=400, detail="Package spec is required.")
    background_tasks.add_task(_install_task, spec)
    return {"status": "installing"}


@router.delete("/{name}")
def uninstall_package(name: str):
    try:
        optional_deps.uninstall_custom(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "uninstalled"}


@router.get("/logs")
def get_logs(limit: int = 200):
    # "dependency" only — this panel is about "why couldn't I install
    # something", not a firehose of every warning/error anywhere in the
    # app (RAM checks, expired OAuth tokens, workflow failures all log
    # through the same root logger — see log_buffer's own note).
    return {"logs": log_buffer.get_recent(limit, category="dependency")}


@router.delete("/logs/clear")
def clear_logs():
    log_buffer.clear()
    return {"status": "cleared"}
