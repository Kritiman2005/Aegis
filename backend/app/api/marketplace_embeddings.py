"""
Aegis — Marketplace Embedding Models API (/api/marketplace/embeddings)

Backs the Marketplace's Embedding Models category: browse fastembed's own
supported-model catalog (app.core.embeddings.registry), download one
(background task + progress broadcast, same pattern as MCP connects and
Marketplace database installs), list/delete what's installed. A workflow
"vector" node (app.core.workflows.engine._run_vector_node) references a
downloaded model by its id — app.core.embeddings.manager resolves that to
a ready fastembed.TextEmbedding instance.
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
from app.core.embeddings.registry import get_catalog_entry, list_catalog
from app.core.hf_search import search_models
from app.db.database import SessionLocal, get_db
from app.db.models import EmbeddingModelRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/marketplace/embeddings", tags=["marketplace-embeddings"])

_data_dir = os.environ.get("AEGIS_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
EMBEDDINGS_DIR = Path(_data_dir) / "embedding_models" if _data_dir else BASE_DIR / "embedding_models"
EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)


class DownloadEmbeddingRequest(BaseModel):
    model_id: str
    # Only used when model_id isn't one of fastembed's own supported models —
    # lets a user pull in ANY sentence-transformers-compatible embedding
    # model from Hugging Face, loaded through sentence-transformers instead
    # (fastembed itself only runs models from its own fixed, ONNX-converted
    # list — see app.core.embeddings.registry's module docstring).
    display_name: Optional[str] = None
    model_config = {"defer_build": True}


@router.get("/catalog")
def get_catalog():
    return {"models": list_catalog()}


@router.get("/search")
def search_hf_embeddings(q: str, limit: int = 20):
    """
    Live-searches Hugging Face for any embedding model beyond fastembed's
    fixed catalog above — tagged "sentence-transformers" since that's what
    /download's sentence_transformers fallback backend actually needs to
    load it. Read-only; nothing installs from a search.
    """
    try:
        return {"models": search_models(q, filter_tag="sentence-transformers", limit=limit)}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("")
def list_installed(db: Session = Depends(get_db)):
    rows = db.query(EmbeddingModelRegistry).order_by(EmbeddingModelRegistry.created_at.desc()).all()
    return {"models": [_serialize(r) for r in rows]}


@router.delete("/{model_row_id}")
def delete_installed(model_row_id: int, db: Session = Depends(get_db)):
    row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.id == model_row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Embedding model not found.")

    shutil.rmtree(row.cache_dir, ignore_errors=True)
    db.delete(row)
    db.commit()
    return {"message": f"Deleted '{row.display_name}'."}


# In-memory current-progress-by-row, read by list_installed below so
# MarketplaceView's existing "poll the list every 2.5s while anything is
# downloading" mechanism (no websocket wiring in that component at all)
# picks up real numbers for free, with no frontend plumbing beyond reading
# a couple of extra fields. Cleared once a row leaves "downloading" either
# way — a stale leftover here would otherwise silently reappear if the
# same row id were ever reused.
_progress_by_row: Dict[int, Dict[str, Any]] = {}


async def _download_task(row_id: int, model_id: str, cache_dir: str, backend: str = "fastembed") -> None:
    async def broadcast(status: str, **kwargs) -> None:
        if status == "running" and "downloaded_bytes" in kwargs:
            _progress_by_row[row_id] = kwargs
        else:
            _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "embedding_download_progress", "model_row_id": row_id, "status": status, **kwargs})

    await broadcast("running", message=f"Downloading {model_id}…")
    try:
        import asyncio
        import anyio
        from app.core.hf_download_progress import DownloadProgressPoller, get_expected_total_bytes

        loop = asyncio.get_running_loop()

        async def on_progress(downloaded: int, total: int) -> None:
            await broadcast("running", downloaded_bytes=downloaded, total_bytes=total, progress=round(downloaded / total * 100, 1))

        real_dim: Optional[int] = None
        if backend == "sentence_transformers":
            from sentence_transformers import SentenceTransformer

            # Real progress: huggingface_hub exposes no byte-level progress
            # callback through its public API (see hf_download_progress.py's
            # module docstring for why) — poll the cache dir on disk instead
            # while SentenceTransformer's own download runs. This path is a
            # fully custom repo (no catalog entry, hence no known real
            # size to fall back on like marketplace_rerankers.py does) — a
            # repo carrying redundant alternate formats (TF/ONNX/multiple
            # precisions) SentenceTransformer's loader never touches can
            # make this an overestimate, same risk documented there.
            total = await anyio.to_thread.run_sync(lambda: get_expected_total_bytes(model_id))
            poller = DownloadProgressPoller(cache_dir, model_id, total, on_progress, loop)
            poller.start()
            try:
                # A custom repo's true embedding dimension isn't known until
                # the model's actually loaded — the row started with a 0
                # placeholder (see download_model below), filled in here.
                st_model = await anyio.to_thread.run_sync(lambda: SentenceTransformer(model_id, cache_folder=cache_dir))
            finally:
                poller.stop()
            real_dim = st_model.get_sentence_embedding_dimension()
        else:
            from fastembed import TextEmbedding

            # Same polling approach, sized to fastembed's OWN file selection
            # (config/tokenizer files + this model's specific model_file/
            # additional_files — not the whole repo, which can also contain
            # quantizations fastembed never touches) so the percentage
            # tracks exactly what's actually being downloaded, not more.
            catalog_entry = next(
                (m for m in TextEmbedding.list_supported_models() if m["model"] == model_id), None
            )
            if catalog_entry:
                hf_source = catalog_entry["sources"]["hf"]
                allow_filenames = [
                    "config.json", "tokenizer.json", "tokenizer_config.json",
                    "special_tokens_map.json", "preprocessor_config.json",
                    catalog_entry["model_file"], *catalog_entry["additional_files"],
                ]
                total = await anyio.to_thread.run_sync(lambda: get_expected_total_bytes(hf_source, allow_filenames))
                poller = DownloadProgressPoller(cache_dir, hf_source, total, on_progress, loop)
                poller.start()
                try:
                    await anyio.to_thread.run_sync(lambda: TextEmbedding.download_files_from_huggingface(
                        hf_source, cache_dir=cache_dir, extra_patterns=[catalog_entry["model_file"], *catalog_entry["additional_files"]],
                    ))
                finally:
                    poller.stop()
            await anyio.to_thread.run_sync(lambda: TextEmbedding(model_name=model_id, cache_dir=cache_dir))

        with SessionLocal() as db:
            row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.id == row_id).first()
            if row:
                row.status = "downloaded"
                if real_dim:
                    row.dim = real_dim
                db.commit()
        _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "embedding_download_complete", "model_row_id": row_id})

    except Exception as e:
        logger.error(f"Embedding model download {row_id} ({model_id}) failed: {e}")
        with SessionLocal() as db:
            row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.id == row_id).first()
            if row:
                row.status = "failed"
                row.error_message = str(e)
                db.commit()
        _progress_by_row.pop(row_id, None)
        await manager.broadcast_json({"type": "embedding_download_failed", "model_row_id": row_id, "message": str(e)})


@router.post("/download")
def download_model(req: DownloadEmbeddingRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    entry = get_catalog_entry(req.model_id)
    # Not in fastembed's supported-models list — treat it as a custom
    # Hugging Face repo id, loaded via sentence-transformers instead (which,
    # unlike fastembed, isn't limited to a fixed list of ONNX-converted
    # models). Its real dimension isn't known until the download finishes —
    # see _download_task — so dim starts at 0, same "unknown until it's
    # actually loaded" placeholder the model's status already communicates.
    backend = "fastembed" if entry else "sentence_transformers"
    display_name = entry["display_name"] if entry else (req.display_name or req.model_id)
    dim = entry["dim"] if entry else 0
    size_gb = entry["size_gb"] if entry else None

    existing = db.query(EmbeddingModelRegistry).filter(
        EmbeddingModelRegistry.model_id == req.model_id, EmbeddingModelRegistry.status != "failed"
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{display_name}' is already installed or installing.")

    instance_dir = EMBEDDINGS_DIR / uuid.uuid4().hex[:12]
    instance_dir.mkdir(parents=True, exist_ok=True)

    row = EmbeddingModelRegistry(
        model_id=req.model_id, display_name=display_name, dim=dim,
        size_gb=size_gb, cache_dir=str(instance_dir), status="downloading", backend=backend,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background_tasks.add_task(_download_task, row.id, req.model_id, str(instance_dir), backend)
    return _serialize(row)


def _serialize(row: EmbeddingModelRegistry) -> Dict[str, Any]:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "display_name": row.display_name,
        "dim": row.dim,
        "size_gb": row.size_gb,
        "backend": row.backend,
        "status": row.status,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # Real download progress, when this row is currently downloading —
        # see _progress_by_row's own docstring for why this rides on the
        # existing polled list instead of a websocket. Absent otherwise.
        **_progress_by_row.get(row.id, {}),
    }
