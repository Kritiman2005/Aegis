import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from huggingface_hub import HfApi
import httpx
import certifi

from app.core.connection_manager import manager
from app.db.database import get_db
from sqlalchemy.orm import Session
from app.db.models import ModelRegistry
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hub", tags=["Model Hub"])

hf_api = HfApi()
BASE_DIR = Path(__file__).resolve().parent.parent.parent
_data_dir = os.environ.get("AEGIS_DATA_DIR")
MODELS_DIR = Path(_data_dir) / "models" if _data_dir else BASE_DIR / "models"


def read_gguf_context_length(file_path: Path) -> Optional[int]:
    """
    Read the model's real trained context length straight from the GGUF file's
    own metadata header (e.g. "qwen2.context_length", "llama.context_length")
    — this only parses the header, not the multi-GB tensor data, so it's fast
    regardless of model size. Returns None if the key can't be found/parsed,
    so the caller can fall back to a safe default rather than crash.
    """
    try:
        from gguf import GGUFReader
        reader = GGUFReader(str(file_path))
        for key, field in reader.fields.items():
            if key.endswith(".context_length"):
                return int(field.parts[field.data[0]][0])
    except Exception as e:
        logger.warning(f"Could not read context_length from GGUF metadata for {file_path}: {e}")
    return None

class DownloadRequest(BaseModel):
    repo_id: str
    filename: str

def get_db_session():
    db = next(get_db())
    try:
        yield db
    finally:
        db.close()


@router.get("/recommendation")
def get_recommendation(db: Session = Depends(get_db_session)):
    """
    Recommend one model from the curated catalog based on the machine's total
    RAM (à la AnythingLLM's setup flow), so a new user gets a concrete
    download/skip choice instead of an empty search box. If the recommended
    model is already downloaded, say so instead of prompting to re-download.
    """
    import psutil
    from app.core.model_catalog import recommend_model

    total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    entry = recommend_model(total_ram_gb)

    already_downloaded = db.query(ModelRegistry).filter(
        ModelRegistry.repo_id == entry.repo_id,
        ModelRegistry.filename == entry.filename,
        ModelRegistry.status == "downloaded",
    ).first() is not None

    return {
        "ram_total_gb": round(total_ram_gb, 1),
        "model": {
            "key": entry.key,
            "display_name": entry.display_name,
            "repo_id": entry.repo_id,
            "filename": entry.filename,
            "approx_download_gb": entry.approx_download_gb,
            "description": entry.description,
        },
        "already_downloaded": already_downloaded,
    }


@router.get("/search")
def search_models(q: str = "", limit: int = 20):
    """
    Search Hugging Face Hub for GGUF models using the REST API to avoid SDK version fragility.
    Returns a list of models with their available .gguf files.
    """
    try:
        search_q = q if q else "gguf"
        
        # Use httpx directly to hit the HF REST API. This is immune to huggingface_hub 
        # python SDK version differences (like `direction` or `tags` kwargs breaking).
        url = "https://huggingface.co/api/models"
        params = {
            "search": search_q,
            "filter": "gguf",
            "limit": limit,
            "sort": "downloads",
            "direction": -1,
            "full": "False"
        }
        
        # Explicitly point at certifi's bundled CA file rather than trusting
        # Python's default SSL context — PyInstaller doesn't always locate the
        # system cert store correctly (notably on Windows), which used to cause
        # [SSL: CERTIFICATE_VERIFY_FAILED]. certifi.where() is bundled into the
        # packaged app via main.spec's collect_data_files('certifi').
        res = httpx.get(url, params=params, timeout=15.0, verify=certifi.where())
        res.raise_for_status()
        models_data = res.json()
        
        results = []
        for model in models_data:
            results.append({
                "id": model.get("id"),
                "author": model.get("author"),
                "downloads": model.get("downloads", 0),
                "likes": model.get("likes", 0),
                "tags": model.get("tags", []),
            })
            
        return {"models": results}
    except Exception as e:
        logger.error(f"Error searching models via REST API: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/repo/{repo_id:path}")
def get_repo_files(repo_id: str):
    """
    Get the list of .gguf files and their sizes for a specific repository.
    """
    try:
        info = hf_api.model_info(repo_id=repo_id, files_metadata=True)
        gguf_files = []
        for file in info.siblings:
            if file.rfilename.endswith(".gguf"):
                gguf_files.append({
                    "filename": file.rfilename,
                    "size": file.size,
                })
                
        # Sort by size ascending (typically smaller quants first)
        gguf_files.sort(key=lambda x: x["size"] if x.get("size") else 0)
        return {"repo_id": repo_id, "files": gguf_files}
    except Exception as e:
        logger.error(f"Error fetching repo files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def download_file_task(repo_id: str, filename: str, file_path: Path, model_id: int):
    """
    Background task to stream a file download, broadcast progress, and update the DB.
    """
    url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    downloaded_bytes = 0
    total_bytes = 1  # prevent division by zero initially
    
    # Notify start
    await manager.broadcast_json({
        "type": "download_progress",
        "repo_id": repo_id,
        "filename": filename,
        "progress": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0
    })

    try:
        # Use a generous timeout for large files; verify against certifi's bundled
        # CA file (see search_models() above for why this isn't verify=False).
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None), verify=certifi.where()) as client:
            async with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                total_bytes = int(response.headers.get("Content-Length", 0))
                
                # Stream to disk
                with open(file_path, "wb") as f:
                    last_broadcast = 0
                    async for chunk in response.aiter_bytes(chunk_size=8192 * 16):
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        
                        # Throttle broadcasts to roughly every 1% or 10MB to avoid spamming the websocket
                        if total_bytes > 0:
                            percent = (downloaded_bytes / total_bytes) * 100
                            if percent - last_broadcast > 0.5:
                                last_broadcast = percent
                                await manager.broadcast_json({
                                    "type": "download_progress",
                                    "repo_id": repo_id,
                                    "filename": filename,
                                    "progress": round(percent, 1),
                                    "downloaded_bytes": downloaded_bytes,
                                    "total_bytes": total_bytes
                                })

        # Download complete! Update DB
        from app.db.database import SessionLocal
        db = SessionLocal()
        model_entry = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
        if model_entry:
            model_entry.status = "downloaded"
            model_entry.file_size_bytes = downloaded_bytes

            real_context_length = read_gguf_context_length(file_path)
            if real_context_length:
                model_entry.context_length = real_context_length
                logger.info(f"Read real context length from GGUF metadata: {real_context_length}")
            else:
                logger.warning(
                    f"Could not determine real context length for {filename} — "
                    f"keeping the default of {model_entry.context_length}."
                )

            db.commit()
        db.close()
        
        await manager.broadcast_json({
            "type": "download_complete",
            "repo_id": repo_id,
            "filename": filename,
            "file_path": str(file_path)
        })
        logger.info(f"Successfully downloaded {filename} to {file_path}")

    except Exception as e:
        logger.error(f"Download failed for {filename}: {e}")
        # Clean up partial file
        if file_path.exists():
            file_path.unlink()
            
        from app.db.database import SessionLocal
        db = SessionLocal()
        model_entry = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
        if model_entry:
            model_entry.status = "failed"
            db.commit()
        db.close()
        
        await manager.broadcast_json({
            "type": "download_failed",
            "repo_id": repo_id,
            "filename": filename,
            "error": str(e)
        })


@router.post("/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db_session)):
    """
    Start downloading a GGUF model from Hugging Face in the background.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = MODELS_DIR / req.filename
    
    # Check if we already have it
    if file_path.exists():
        existing = db.query(ModelRegistry).filter(ModelRegistry.file_path == str(file_path)).first()
        if existing and existing.status == "downloaded":
            return {"status": "already_downloaded", "file_path": str(file_path)}
            
    # Create or update DB entry
    model_name = req.repo_id.split("/")[-1] + "-" + req.filename.replace(".gguf", "")
    
    model_entry = db.query(ModelRegistry).filter(ModelRegistry.filename == req.filename).first()
    if not model_entry:
        model_entry = ModelRegistry(
            name=model_name.lower()[:50],  # simple slug
            display_name=f"{req.repo_id} ({req.filename})",
            repo_id=req.repo_id,
            filename=req.filename,
            file_path=str(file_path),
            status="downloading",
            chat_format="chatml", # Default guess
            context_length=4096
        )
        db.add(model_entry)
        db.commit()
        db.refresh(model_entry)
    else:
        model_entry.status = "downloading"
        model_entry.file_path = str(file_path)
        db.commit()
        
    # Launch background task
    background_tasks.add_task(
        download_file_task, 
        req.repo_id, 
        req.filename, 
        file_path, 
        model_entry.id
    )
    
    return {"status": "download_started", "model_id": model_entry.id}

@router.get("/downloaded")
def list_downloaded_models(db: Session = Depends(get_db_session)):
    """List all downloaded/downloading models from SQLite."""
    models = db.query(ModelRegistry).order_by(ModelRegistry.created_at.desc()).all()
    return {"models": [{
        "id": m.id,
        "name": m.name,
        "display_name": m.display_name,
        "repo_id": m.repo_id,
        "filename": m.filename,
        "status": m.status,
        "file_size_bytes": m.file_size_bytes,
        "is_active": m.is_active,
        "context_length": m.context_length
    } for m in models]}
