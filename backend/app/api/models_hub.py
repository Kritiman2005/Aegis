import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from huggingface_hub import HfApi
import httpx

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
    # The paired mmproj (vision tower) filename from the same repo, if the
    # user is downloading a vision model — see get_repo_files' mmproj_files.
    mmproj_filename: Optional[str] = None

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
        
        # No explicit verify= here — main.py's truststore.inject_into_ssl()
        # (called at process startup, before any SSLContext is created)
        # already makes every SSL connection use the OS's own native trust
        # evaluation instead of a bundled CA file. That's a strictly better
        # fix for the original problem this used to work around
        # (PyInstaller/Windows sometimes failing to locate the system cert
        # store in a frozen build) — truststore queries Windows' CryptoAPI
        # directly rather than needing any file to exist on disk at all —
        # and it ALSO correctly trusts a locally-installed interception CA
        # (corporate VPN/antivirus doing TLS inspection), which a fixed
        # certifi.where() bundle never would.
        res = httpx.get(url, params=params, timeout=15.0)
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

    Separates out any "mmproj" file (the CLIP-style vision tower a vision
    model needs alongside its main weights — see llm_manager.py's
    MTMDChatHandler wiring) into its own list rather than the main quant
    list — it's a required companion file, not a normal quantization choice,
    so it shouldn't be offered next to the model's own quants as if picking
    one excludes the others.
    """
    try:
        info = hf_api.model_info(repo_id=repo_id, files_metadata=True)
        gguf_files = []
        mmproj_files = []
        for file in info.siblings:
            if not file.rfilename.endswith(".gguf"):
                continue
            entry = {"filename": file.rfilename, "size": file.size}
            if "mmproj" in file.rfilename.lower():
                mmproj_files.append(entry)
            else:
                gguf_files.append(entry)

        # Sort by size ascending (typically smaller quants first)
        gguf_files.sort(key=lambda x: x["size"] if x.get("size") else 0)
        mmproj_files.sort(key=lambda x: x["size"] if x.get("size") else 0)
        return {"repo_id": repo_id, "files": gguf_files, "mmproj_files": mmproj_files}
    except Exception as e:
        logger.error(f"Error fetching repo files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def download_file_task(repo_id: str, filename: str, file_path: Path, model_id: int, kind: str = "main"):
    """
    Background task to stream a file download, broadcast progress, and update the DB.

    `kind` distinguishes a model's main weights file from its paired mmproj
    (vision tower) file — both are downloaded via this same streaming loop,
    but they update different columns on the same ModelRegistry row on
    completion/failure (see start_download, which schedules a "mmproj" kind
    task alongside the "main" one for a vision download).
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
        # Use a generous timeout for large files; no explicit verify= — see
        # search_models() above for why (truststore.inject_into_ssl(), not
        # verify=False).
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None)) as client:
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
            if kind == "mmproj":
                model_entry.mmproj_path = str(file_path)
                model_entry.mmproj_status = "downloaded"
            else:
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
            "file_path": str(file_path),
            "kind": kind,
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
            if kind == "mmproj":
                model_entry.mmproj_status = "failed"
            else:
                model_entry.status = "failed"
            db.commit()
        db.close()

        await manager.broadcast_json({
            "type": "download_failed",
            "repo_id": repo_id,
            "filename": filename,
            "error": str(e),
            "kind": kind,
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
            context_length=4096,
            is_vision=bool(req.mmproj_filename),
            mmproj_filename=req.mmproj_filename,
            mmproj_status="downloading" if req.mmproj_filename else None,
        )
        db.add(model_entry)
        db.commit()
        db.refresh(model_entry)
    else:
        model_entry.status = "downloading"
        model_entry.file_path = str(file_path)
        if req.mmproj_filename:
            model_entry.is_vision = True
            model_entry.mmproj_filename = req.mmproj_filename
            model_entry.mmproj_status = "downloading"
        db.commit()

    # Launch background task(s) — the main weights file, plus its paired
    # mmproj (vision tower) file when this is a vision download. Both write
    # to the same ModelRegistry row (see download_file_task's `kind` param).
    background_tasks.add_task(
        download_file_task,
        req.repo_id,
        req.filename,
        file_path,
        model_entry.id,
    )
    if req.mmproj_filename:
        mmproj_path = MODELS_DIR / req.mmproj_filename
        background_tasks.add_task(
            download_file_task,
            req.repo_id,
            req.mmproj_filename,
            mmproj_path,
            model_entry.id,
            "mmproj",
        )

    return {"status": "download_started", "model_id": model_entry.id}

@router.get("/downloaded")
def list_downloaded_models(db: Session = Depends(get_db_session)):
    """List all downloaded/downloading models from SQLite."""
    from app.core.llm_manager import resolve_effective_n_ctx
    from app.db.crud import get_model_usage_by_workflows

    models = db.query(ModelRegistry).order_by(ModelRegistry.created_at.desc()).all()
    usage_by_model = get_model_usage_by_workflows(db)
    return {"models": [{
        "id": m.id,
        "name": m.name,
        "display_name": m.display_name,
        "repo_id": m.repo_id,
        "filename": m.filename,
        "status": m.status,
        "file_size_bytes": m.file_size_bytes,
        "is_active": m.is_active,
        "context_length": m.context_length,
        # The REAL context window this model actually loads with — capped
        # well below context_length (its native/trained max) unless THIS
        # model has its own context_cap override set. See
        # llm_manager.resolve_effective_n_ctx's docstring for why the raw
        # context_length alone is misleading as a "what will I actually
        # get" ceiling.
        "effective_context_length": resolve_effective_n_ctx(m.context_length, m.name),
        "context_cap": m.context_cap,
        "is_vision": m.is_vision,
        "mmproj_filename": m.mmproj_filename,
        "mmproj_status": m.mmproj_status,
        "used_by_workflows": usage_by_model.get(m.name, []),
    } for m in models]}


class ContextCapRequest(BaseModel):
    # None clears the override, falling back to the safe default again.
    n_ctx: Optional[int] = None


@router.post("/{model_id}/context-cap")
def set_model_context_cap(model_id: int, req: ContextCapRequest, db: Session = Depends(get_db_session)):
    """
    Set (or clear) this ONE model's own context window cap override — see
    ModelRegistry.context_cap and llm_manager.resolve_effective_n_ctx. Each
    model gets its own cap rather than one app-wide value, since a small
    model and a large one legitimately want different limits. If this
    model is currently loaded, only it (not every other loaded model) is
    unloaded so it picks up the new cap next time it's actually used.
    """
    model = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")

    if req.n_ctx is not None:
        if req.n_ctx < 512:
            raise HTTPException(status_code=400, detail="Context cap must be at least 512 tokens.")
        if model.context_length and req.n_ctx > model.context_length:
            raise HTTPException(
                status_code=400,
                detail=f"Context cap can't exceed this model's native context ({model.context_length:,} tokens).",
            )

    from app.api.context_config import is_llm_busy
    if is_llm_busy():
        raise HTTPException(status_code=409, detail="Cannot change the context cap while a generation is in progress.")

    model.context_cap = req.n_ctx
    db.commit()

    from app.core.agents.chat import get_llm_manager
    llm_manager = get_llm_manager()
    if model.name in llm_manager.loaded_models:
        llm_manager.unload_model(model.name)

    return {"success": True, "context_cap": model.context_cap}


@router.delete("/{model_id}")
def delete_downloaded_model(model_id: int, db: Session = Depends(get_db_session)):
    """
    Deletes a downloaded model: removes its GGUF file (and paired mmproj
    file, for a vision model) from disk, then the ModelRegistry row.
    Refuses to delete the currently-active (loaded-in-RAM) model — llama.cpp
    already has that file memory-mapped, and eject-then-delete is one clear
    action instead of this endpoint silently ejecting on the user's behalf.
    """
    model = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")
    if model.is_active:
        raise HTTPException(status_code=400, detail=f"'{model.display_name}' is loaded in RAM — eject it first, then delete.")

    for path in (model.file_path, model.mmproj_path):
        if path:
            Path(path).unlink(missing_ok=True)

    db.delete(model)
    db.commit()
    return {"message": f"Deleted '{model.display_name}'."}
