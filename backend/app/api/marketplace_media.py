"""
Aegis — Marketplace Media Engines API (/api/marketplace/media-engines)

Backs the Marketplace's OCR/Transcription category and the Workflow
canvas's Media tool nodes' (transcribe_media, extract_image_text) engine
picker — see app.core.media_engines' module docstring for the full
scoping story (Workflow-node-only; the chat composer's mic transcription
is untouched by this).

Same download-with-progress shape as marketplace_embeddings.py/
marketplace_rerankers.py, generalized over two capabilities ("ocr",
"transcription") instead of one dedicated router each. Both get the same
free-text Hugging Face search + "install any repo id" escape hatch
embeddings/rerankers already have (search + /install-custom below):
transcription accepts any CTranslate2-format Whisper conversion (
faster_whisper.WhisperModel already accepts a raw repo id in place of a
known size name), OCR is scoped to models tagged "image-to-text" (run
through transformers.pipeline — see app.core.media_engines.run_ocr's own
comment for why that specific tag, not every OCR-adjacent one Hugging Face
has: captioning and document-QA models share overlapping tags but need a
different call shape transformers.pipeline("image-to-text", ...) can't
give them).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core import media_engines, optional_deps
from app.core.connection_manager import manager
from app.core.hf_search import search_models
from app.core.friendly_errors import humanize_exception

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/marketplace/media-engines", tags=["marketplace-media-engines"])

_VALID_CAPABILITIES = ("ocr", "transcription")

# In-memory current-progress-by-engine — see marketplace_embeddings.py's
# _progress_by_row for why this rides on the existing polled list instead
# of relying solely on the websocket broadcast. Keyed "capability:engine_id".
_progress: Dict[str, Dict[str, Any]] = {}


def _check_capability(capability: str) -> None:
    if capability not in _VALID_CAPABILITIES:
        raise HTTPException(status_code=404, detail=f"Unknown capability: '{capability}'")


@router.get("")
def list_engines(capability: str):
    """Every catalog engine for this capability, each carrying its live
    install/progress state, plus any custom (Hugging-Face-search-installed)
    engine beyond the catalog."""
    _check_capability(capability)
    catalog = media_engines.OCR_ENGINES if capability == "ocr" else media_engines.TRANSCRIPTION_ENGINES
    out = []
    for entry in catalog:
        row = dict(entry)
        row["installed"] = media_engines.is_engine_installed(capability, entry["id"])
        row.update(_progress.get(f"{capability}:{entry['id']}", {}))
        out.append(row)
    for custom_id in media_engines.list_custom_engine_ids(capability):
        row = {
            "id": custom_id, "display_name": custom_id, "description": "Custom model, installed from Hugging Face search.",
            "default": False, "downloadable": True, "custom": True, "installed": True,
        }
        row.update(_progress.get(f"{capability}:{custom_id}", {}))
        out.append(row)
    return {"engines": out}


async def _download_whisper(capability: str, engine_id: str, hf_size: str) -> None:
    key = f"{capability}:{engine_id}"

    async def broadcast(status: str, **kwargs) -> None:
        if status == "running":
            _progress[key] = {"status": status, **kwargs}
        else:
            _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_progress", "capability": capability, "engine_id": engine_id, "status": status, **kwargs})

    await broadcast("running", message=f"Downloading {engine_id}…")
    try:
        import asyncio
        import anyio
        from faster_whisper import WhisperModel
        from app.core.hf_download_progress import DownloadProgressPoller, get_expected_total_bytes

        loop = asyncio.get_running_loop()
        repo_id = hf_size if "/" in hf_size else f"Systran/faster-whisper-{hf_size}"
        cache_dir = str(media_engines.engine_dir(capability, engine_id))

        async def on_progress(downloaded: int, total: int) -> None:
            await broadcast("running", downloaded_bytes=downloaded, total_bytes=total, progress=round(downloaded / total * 100, 1))

        total = await anyio.to_thread.run_sync(lambda: get_expected_total_bytes(repo_id))
        poller = DownloadProgressPoller(cache_dir, repo_id, total, on_progress, loop)
        poller.start()
        try:
            await anyio.to_thread.run_sync(lambda: WhisperModel(
                hf_size, download_root=cache_dir, device="cpu", compute_type="int8",
            ))
        finally:
            poller.stop()

        media_engines.mark_installed(capability, engine_id)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_complete", "capability": capability, "engine_id": engine_id})
    except Exception as e:
        friendly = humanize_exception(e, context=f"downloading '{engine_id}'")
        logger.info(f"Media engine download {key} raw error: {e}")
        logger.error(friendly)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_failed", "capability": capability, "engine_id": engine_id, "message": friendly})


async def _install_rapidocr(capability: str, engine_id: str) -> None:
    """
    RapidOCR is a plain pip package (unlike EasyOCR/Whisper, no separate
    weight download — its ONNX models ship inside the wheel itself), so
    "installing" it means pip-installing rapidocr-onnxruntime, the exact
    same mechanism the Dependencies panel's own install box uses
    (app.core.optional_deps.install_custom_sync) — not a download-with-
    progress task like this module's other two _download_* functions.
    No real byte-level progress to report either way, so this is
    status-only (running/downloaded/failed), same UI contract as those.
    """
    key = f"{capability}:{engine_id}"

    async def broadcast(status: str, **kwargs) -> None:
        if status == "running":
            _progress[key] = {"status": status, **kwargs}
        else:
            _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_progress", "capability": capability, "engine_id": engine_id, "status": status, **kwargs})

    await broadcast("running", message="Installing RapidOCR…")
    try:
        import anyio
        from app.core import optional_deps

        await anyio.to_thread.run_sync(optional_deps.install_custom_sync, "rapidocr-onnxruntime")
        media_engines.mark_installed(capability, engine_id)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_complete", "capability": capability, "engine_id": engine_id})
    except Exception as e:
        friendly = humanize_exception(e, context="installing RapidOCR")
        logger.info(f"Media engine install {key} raw error: {e}")
        logger.error(friendly)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_failed", "capability": capability, "engine_id": engine_id, "message": friendly})


async def _download_easyocr(capability: str, engine_id: str) -> None:
    """
    EasyOCR downloads its detector+recognizer weights via its own plain
    HTTP fetch (not huggingface_hub), so there's no equivalent of
    app.core.hf_download_progress's blob-polling to get real byte
    progress from — status-only (running/downloaded/failed). Still writes
    into _progress (not just the websocket broadcast) so MarketplaceView's
    plain HTTP polling — which has no websocket wiring at all — sees the
    in-progress state too, same as the whisper path.
    """
    key = f"{capability}:{engine_id}"

    async def broadcast(status: str, **kwargs) -> None:
        if status == "running":
            _progress[key] = {"status": status, **kwargs}
        else:
            _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_progress", "capability": capability, "engine_id": engine_id, "status": status, **kwargs})

    await broadcast("running", message="Downloading EasyOCR model weights…")
    try:
        import anyio
        import easyocr

        def _download():
            easyocr.Reader(
                ["en"], gpu=False,
                model_storage_directory=str(media_engines.engine_dir(capability, engine_id)),
                download_enabled=True,
            )
        await anyio.to_thread.run_sync(_download)
        media_engines.mark_installed(capability, engine_id)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_complete", "capability": capability, "engine_id": engine_id})
    except Exception as e:
        friendly = humanize_exception(e, context=f"downloading '{engine_id}'")
        logger.info(f"Media engine download {key} raw error: {e}")
        logger.error(friendly)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_failed", "capability": capability, "engine_id": engine_id, "message": friendly})


async def _download_hf_ocr_model(engine_id: str) -> None:
    """Custom (Hugging-Face-search-installed) OCR engine — engine_id is
    always a raw repo id here (see media_engines.list_custom_engine_ids),
    loaded through transformers' image-to-text pipeline, which uses the
    same huggingface_hub cache-dir download mechanism as the
    sentence_transformers backend marketplace_embeddings.py's custom-repo
    path already relies on, so the exact same progress-polling approach
    (app.core.hf_download_progress) applies unchanged."""
    key = f"ocr:{engine_id}"

    async def broadcast(status: str, **kwargs) -> None:
        if status == "running":
            _progress[key] = {"status": status, **kwargs}
        else:
            _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_progress", "capability": "ocr", "engine_id": engine_id, "status": status, **kwargs})

    await broadcast("running", message=f"Downloading {engine_id}…")
    try:
        import asyncio
        import anyio
        from transformers import pipeline
        from app.core.hf_download_progress import DownloadProgressPoller, get_expected_total_bytes

        loop = asyncio.get_running_loop()
        cache_dir = str(media_engines.engine_dir("ocr", engine_id))

        async def on_progress(downloaded: int, total: int) -> None:
            await broadcast("running", downloaded_bytes=downloaded, total_bytes=total, progress=round(downloaded / total * 100, 1))

        total = await anyio.to_thread.run_sync(lambda: get_expected_total_bytes(engine_id))
        poller = DownloadProgressPoller(cache_dir, engine_id, total, on_progress, loop)
        poller.start()
        try:
            # model_kwargs, not a top-level cache_dir — see
            # app.core.media_engines.run_ocr's matching comment for why.
            await anyio.to_thread.run_sync(lambda: pipeline(
                "image-to-text", model=engine_id, device=-1,
                model_kwargs={"cache_dir": cache_dir},
            ))
        finally:
            poller.stop()

        media_engines.mark_installed("ocr", engine_id)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_complete", "capability": "ocr", "engine_id": engine_id})
    except Exception as e:
        friendly = humanize_exception(e, context=f"downloading '{engine_id}'")
        logger.info(f"Media engine download {key} raw error: {e}")
        logger.error(friendly)
        _progress.pop(key, None)
        await manager.broadcast_json({"type": "media_engine_download_failed", "capability": "ocr", "engine_id": engine_id, "message": friendly})


@router.post("/{capability}/{engine_id}/install")
def install_engine(capability: str, engine_id: str, background_tasks: BackgroundTasks):
    _check_capability(capability)
    entry = media_engines.get_entry(capability, engine_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown {capability} engine: '{engine_id}'")
    if not entry.get("downloadable"):
        raise HTTPException(status_code=400, detail=f"'{engine_id}' is already bundled — nothing to install.")
    if media_engines.is_engine_installed(capability, engine_id):
        raise HTTPException(status_code=400, detail=f"'{entry['display_name']}' is already installed.")

    if capability == "transcription":
        background_tasks.add_task(_download_whisper, capability, engine_id, entry["hf_size"])
    elif engine_id == "rapidocr":
        # Its own pip install (see _install_rapidocr) — nothing to
        # pre-check, installing IS the point of clicking this button.
        background_tasks.add_task(_install_rapidocr, capability, engine_id)
    else:
        # EasyOCR — checked BEFORE starting a background task that would
        # only fail later (it also needs torch/torchvision, pulled in
        # transitively by pip installing "easyocr" itself).
        try:
            optional_deps.require_available("easyocr", "EasyOCR")
        except optional_deps.MissingDependencyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        background_tasks.add_task(_download_easyocr, capability, engine_id)
    return {"status": "downloading"}


@router.get("/{capability}/search")
def search_engines(capability: str, q: str, limit: int = 20):
    """Free-text only, same as marketplace_rerankers.py/marketplace_embeddings.py's
    search — no tag filter narrowing results to Aegis's own guess of
    what's "real" OCR/transcription. install-custom still fails loudly on
    an incompatible pick either way, same as it always has; search only
    ever helps discovery, it was never the actual gate."""
    _check_capability(capability)
    try:
        return {"models": search_models(q, limit=limit)}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


class InstallCustomRequest(BaseModel):
    model_id: str


@router.post("/{capability}/install-custom")
def install_custom_engine(capability: str, req: InstallCustomRequest, background_tasks: BackgroundTasks):
    """Installs any Hugging Face repo id directly, beyond the curated
    catalog above — same "search helps discovery, isn't a gate" contract
    as embeddings/rerankers' own custom-repo-id path."""
    _check_capability(capability)
    model_id = req.model_id.strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")
    if media_engines.is_engine_installed(capability, model_id):
        raise HTTPException(status_code=400, detail=f"'{model_id}' is already installed.")

    if capability == "transcription":
        background_tasks.add_task(_download_whisper, capability, model_id, model_id)
    else:
        # Custom HF OCR models run through transformers' image-to-text
        # pipeline (see _download_hf_ocr_model) — checked BEFORE starting a
        # background task that would only fail later.
        try:
            optional_deps.require_available("transformers", "This OCR model")
        except optional_deps.MissingDependencyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        background_tasks.add_task(_download_hf_ocr_model, model_id)
    return {"status": "downloading"}


@router.delete("/{capability}/{engine_id:path}")
def uninstall_engine(capability: str, engine_id: str):
    _check_capability(capability)
    try:
        media_engines.uninstall_engine(capability, engine_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "uninstalled"}
