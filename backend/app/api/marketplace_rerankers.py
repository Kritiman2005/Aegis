"""
Aegis — Marketplace Rerankers API (/api/marketplace/rerankers)

Backs the Marketplace's Rerankers category: browse app.core.rerankers'
curated CATALOG, download one (background task + progress broadcast, same
pattern as Marketplace embedding-model downloads and database installs),
list/delete what's installed. A workflow "reranker" node
(app.core.workflows.engine._run_reranker_node) references a downloaded
model by its id — app.core.rerankers.get_cross_encoder resolves that to a
ready sentence_transformers.CrossEncoder instance.
"""

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.connection_manager import manager
from app.core.rerankers import CATALOG, get_catalog_entry
from app.core.hf_search import search_models
from app.core.friendly_errors import humanize_exception
from app.db.database import SessionLocal, get_db
from app.db.models import RerankerModelRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/marketplace/rerankers", tags=["marketplace-rerankers"])

_data_dir = os.environ.get("AEGIS_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
RERANKERS_DIR = Path(_data_dir) / "reranker_models" if _data_dir else BASE_DIR / "reranker_models"
RERANKERS_DIR.mkdir(parents=True, exist_ok=True)

# app.core.rag.processor.get_reranker's hardcoded model — see
# app.core.bundled_defaults' module docstring for why this needs its own
# synthetic "installed" entry rather than a real RerankerModelRegistry row.
AEGIS_BUNDLED_DEFAULT_ID = "BAAI/bge-reranker-base"


def _bundled_default_entry() -> Optional[Dict[str, Any]]:
    from app.core.bundled_defaults import cached_repo_info
    info = cached_repo_info(AEGIS_BUNDLED_DEFAULT_ID)
    if not info:
        return None
    return {
        "id": "bundled-default",
        "model_id": AEGIS_BUNDLED_DEFAULT_ID,
        "display_name": f"{AEGIS_BUNDLED_DEFAULT_ID} — Aegis's own default",
        "size_gb": round(info["size_on_disk"] / 1024 ** 3, 2),
        "status": "downloaded",
        "error_message": None,
        "created_at": None,
        "bundled": True,
    }


class DownloadRerankerRequest(BaseModel):
    model_id: str
    # Only used when model_id isn't one of CATALOG's curated entries — lets
    # a user pull in ANY sentence-transformers-compatible cross-encoder from
    # Hugging Face, not just the hand-picked list (get_cross_encoder already
    # loads by raw model_id with no catalog restriction; this endpoint was
    # the only thing narrowing it).
    display_name: Optional[str] = None
    model_config = {"defer_build": True}


@router.get("/catalog")
def get_catalog():
    return {"models": CATALOG}


@router.get("/search")
def search_hf_rerankers(q: str, limit: int = 20):
    """
    Live-searches Hugging Face for a cross-encoder beyond the curated CATALOG
    above. Not tag-filtered — unlike embeddings, cross-encoder rerankers
    don't share one consistent HF tag, so a plain free-text search (the same
    permissive approach models_hub.py's LLM search uses) surfaces them
    better than an over-narrow filter would. Read-only; nothing installs
    from a search.
    """
    try:
        return {"models": search_models(q, limit=limit)}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("")
def list_installed(db: Session = Depends(get_db)):
    rows = db.query(RerankerModelRegistry).order_by(RerankerModelRegistry.created_at.desc()).all()
    models = [_serialize(r) for r in rows]
    bundled = _bundled_default_entry()
    if bundled:
        models.insert(0, bundled)
    return {"models": models}


@router.delete("/bundled-default")
def delete_bundled_default():
    """Deletes Aegis's own bundled default reranker's real Hugging Face
    cache — see app.core.bundled_defaults' module docstring. Safe:
    get_reranker() transparently re-downloads it the next time it's
    actually needed, same as a fresh install's first use."""
    from app.core.bundled_defaults import delete_cached_repo
    if not delete_cached_repo(AEGIS_BUNDLED_DEFAULT_ID):
        raise HTTPException(status_code=404, detail="Bundled default reranker isn't currently cached.")
    return {"message": "Deleted Aegis's bundled default reranker."}


@router.delete("/{model_row_id}")
def delete_installed(model_row_id: int, db: Session = Depends(get_db)):
    row = db.query(RerankerModelRegistry).filter(RerankerModelRegistry.id == model_row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Reranker not found.")

    shutil.rmtree(row.cache_dir, ignore_errors=True)
    db.delete(row)
    db.commit()
    return {"message": f"Deleted '{row.display_name}'."}


# In-memory current-progress-by-row — see marketplace_embeddings.py's
# _progress_by_row for why this rides on the existing polled list instead
# of a websocket.
_progress_by_row: Dict[int, Dict[str, Any]] = {}


async def _download_task(row_id: int, model_id: str, cache_dir: str) -> None:
    async def broadcast(status: str, **kwargs) -> None:
        if status == "running" and "downloaded_bytes" in kwargs:
            _progress_by_row[row_id] = kwargs
        else:
            _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "reranker_download_progress", "model_row_id": row_id, "status": status, **kwargs})

    await broadcast("running", message=f"Downloading {model_id}…")
    try:
        import asyncio
        import anyio
        from sentence_transformers import CrossEncoder
        from app.core.hf_download_progress import DownloadProgressPoller, get_expected_total_bytes

        loop = asyncio.get_running_loop()

        async def on_progress(downloaded: int, total: int) -> None:
            await broadcast("running", downloaded_bytes=downloaded, total_bytes=total, progress=round(downloaded / total * 100, 1))

        # Real progress: huggingface_hub exposes no byte-level progress
        # callback through its public API (see hf_download_progress.py's
        # module docstring for why) — poll the cache dir on disk instead
        # while CrossEncoder's own download runs (same approach as
        # marketplace_embeddings.py's sentence_transformers backend).
        #
        # Prefer the catalog's own known size over summing every file HF
        # lists for this repo — confirmed against a real repo
        # (cross-encoder/ms-marco-MiniLM-L-6-v2) that a plain sum can
        # overshoot the real download by 8x+ when a repo carries redundant
        # alternate formats (TF/ONNX/multiple precisions) that
        # CrossEncoder's loader never actually touches, which would make
        # the percentage crawl and never approach 100% before completion.
        entry = get_catalog_entry(model_id)
        if entry and entry.get("size_gb"):
            total = int(entry["size_gb"] * 1024 ** 3)
        else:
            total = await anyio.to_thread.run_sync(lambda: get_expected_total_bytes(model_id))
        poller = DownloadProgressPoller(cache_dir, model_id, total, on_progress, loop)
        poller.start()
        try:
            await anyio.to_thread.run_sync(lambda: CrossEncoder(model_id, cache_folder=cache_dir))
        finally:
            poller.stop()

        with SessionLocal() as db:
            row = db.query(RerankerModelRegistry).filter(RerankerModelRegistry.id == row_id).first()
            if row:
                row.status = "downloaded"
                db.commit()
        _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "reranker_download_complete", "model_row_id": row_id})

    except Exception as e:
        friendly = humanize_exception(e, context=f"downloading '{model_id}'")
        logger.info(f"Reranker download {row_id} ({model_id}) raw error: {e}")
        logger.error(friendly)
        with SessionLocal() as db:
            row = db.query(RerankerModelRegistry).filter(RerankerModelRegistry.id == row_id).first()
            if row:
                row.status = "failed"
                row.error_message = friendly
                db.commit()
        _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "reranker_download_failed", "model_row_id": row_id, "message": friendly})


@router.post("/download")
def download_model(req: DownloadRerankerRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # Every reranker (curated or custom) loads through sentence_transformers.
    # CrossEncoder — check BEFORE starting a background task that would
    # only fail later, so this is instant and shows up as a real popup, not
    # a silent background-task failure the user has to go dig for.
    from app.core.optional_deps import require_available, MissingDependencyError
    try:
        require_available("sentence-transformers", "The reranker")
    except MissingDependencyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    entry = get_catalog_entry(req.model_id)
    # Not in the curated list — treat it as a custom Hugging Face repo id.
    # get_cross_encoder (app.core.rerankers) already loads any model_id via
    # sentence_transformers.CrossEncoder with no catalog check; a bad/
    # incompatible repo id just fails the download below with a real error,
    # same as it would for a curated one.
    display_name = entry["display_name"] if entry else (req.display_name or req.model_id)
    size_gb = entry["size_gb"] if entry else None

    existing = db.query(RerankerModelRegistry).filter(
        RerankerModelRegistry.model_id == req.model_id, RerankerModelRegistry.status != "failed"
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{display_name}' is already installed or installing.")

    instance_dir = RERANKERS_DIR / uuid.uuid4().hex[:12]
    instance_dir.mkdir(parents=True, exist_ok=True)

    row = RerankerModelRegistry(
        model_id=req.model_id, display_name=display_name,
        size_gb=size_gb, cache_dir=str(instance_dir), status="downloading",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background_tasks.add_task(_download_task, row.id, req.model_id, str(instance_dir))
    return _serialize(row)


def _serialize(row: RerankerModelRegistry) -> Dict[str, Any]:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "display_name": row.display_name,
        "size_gb": row.size_gb,
        "status": row.status,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # Real download progress, when this row is currently downloading —
        # see _progress_by_row's own docstring for why this rides on the
        # existing polled list instead of a websocket. Absent otherwise.
        **_progress_by_row.get(row.id, {}),
    }
