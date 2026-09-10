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
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.connection_manager import manager
from app.core.embeddings.registry import get_catalog_entry, list_catalog
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
    model_config = {"defer_build": True}


@router.get("/catalog")
def get_catalog():
    return {"models": list_catalog()}


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


async def _download_task(row_id: int, model_id: str, cache_dir: str) -> None:
    async def broadcast(status: str, **kwargs) -> None:
        await manager.broadcast_json({"type": "embedding_download_progress", "model_row_id": row_id, "status": status, **kwargs})

    await broadcast("running", message=f"Downloading {model_id}…")
    try:
        import anyio
        from fastembed import TextEmbedding

        # fastembed's own downloader has no granular progress callback to
        # hook into (unlike models_hub.py's GGUF downloader) — this is a
        # single blocking step reported as running -> complete/failed,
        # not a percentage bar.
        await anyio.to_thread.run_sync(lambda: TextEmbedding(model_name=model_id, cache_dir=cache_dir))

        with SessionLocal() as db:
            row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.id == row_id).first()
            if row:
                row.status = "downloaded"
                db.commit()
        await manager.broadcast_json({"type": "embedding_download_complete", "model_row_id": row_id})

    except Exception as e:
        logger.error(f"Embedding model download {row_id} ({model_id}) failed: {e}")
        with SessionLocal() as db:
            row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.id == row_id).first()
            if row:
                row.status = "failed"
                row.error_message = str(e)
                db.commit()
        await manager.broadcast_json({"type": "embedding_download_failed", "model_row_id": row_id, "message": str(e)})


@router.post("/download")
def download_model(req: DownloadEmbeddingRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    entry = get_catalog_entry(req.model_id)
    if not entry:
        raise HTTPException(status_code=400, detail=f"Unknown embedding model '{req.model_id}'.")

    existing = db.query(EmbeddingModelRegistry).filter(
        EmbeddingModelRegistry.model_id == req.model_id, EmbeddingModelRegistry.status != "failed"
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{entry['display_name']}' is already installed or installing.")

    instance_dir = EMBEDDINGS_DIR / uuid.uuid4().hex[:12]
    instance_dir.mkdir(parents=True, exist_ok=True)

    row = EmbeddingModelRegistry(
        model_id=req.model_id, display_name=entry["display_name"], dim=entry["dim"],
        size_gb=entry["size_gb"], cache_dir=str(instance_dir), status="downloading",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background_tasks.add_task(_download_task, row.id, req.model_id, str(instance_dir))
    return _serialize(row)


def _serialize(row: EmbeddingModelRegistry) -> Dict[str, Any]:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "display_name": row.display_name,
        "dim": row.dim,
        "size_gb": row.size_gb,
        "status": row.status,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
