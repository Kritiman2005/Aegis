from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from typing import List, Dict, Any, Optional
import os
import shutil
from pathlib import Path
from datetime import datetime

from app.db.database import get_db
from app.db.models import UserDocument
from app.core.rag.processor import ingest_document, delete_document_points
from app.core.connection_manager import manager as ws_manager
import anyio

router = APIRouter(prefix="/api/documents", tags=["Documents"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_data_dir = os.environ.get("AEGIS_DATA_DIR")
UPLOAD_DIR = Path(_data_dir) / "uploads" if _data_dir else BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

import logging
logger = logging.getLogger(__name__)

def process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str) -> tuple[bool, str | None]:
    """
    Background task to extract, chunk, and embed a document.
    Returns (success, error_message) so the caller can report the real outcome —
    previously this swallowed every failure internally and the caller had no way
    to tell success from failure, so the UI always reported "ready" regardless.
    """
    from app.db.database import SessionLocal
    db = SessionLocal()
    doc = db.query(UserDocument).filter(UserDocument.id == doc_id).first()
    if not doc:
        db.close()
        return False, "Document record not found."

    logger.info(f"Starting RAG processing for document: {filename} (ID: {doc_id})")
    try:
        # Call RAG processor
        ingest_document(doc_id, file_path, file_type, filename)
        doc.status = "ready"
        db.commit()
        logger.info(f"Successfully processed and embedded document: {filename}")
        return True, None
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        logger.error(f"Failed to process document {filename}: {e}")
        return False, str(e)
    finally:
        db.close()

_AUDIO_VIDEO_EXTENSIONS = {
    "mp3", "wav", "m4a", "ogg", "flac", "aac", "wma",
    "mp4", "mov", "mkv", "webm", "avi",
}


async def async_process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str, conversation_id: str):
    """Async wrapper to broadcast progress over WebSockets and run the heavy ML ingestion in a separate thread."""
    verb = "Transcribing" if file_type.lower() in _AUDIO_VIDEO_EXTENSIONS else "Ingesting"
    await ws_manager.broadcast_json({"type": "document_progress", "content": f"{verb} {filename} (this may take a moment)..."})
    try:
        # Run blocking processing in a thread pool so we don't freeze FastAPI's async event loop
        success, error_message = await anyio.to_thread.run_sync(
            process_upload_task, doc_id, file_path, file_type, filename
        )
    except Exception as e:
        success, error_message = False, str(e)

    if success:
        await ws_manager.broadcast_json({"type": "document_progress", "content": f"✅ {filename} is ready for chat."})
    else:
        await ws_manager.broadcast_json({"type": "document_progress", "content": f"❌ {filename}: {error_message or 'processing failed'}"})

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    conversation_id: str = Form(...),
    db = Depends(get_db)
):
    """Uploads a document and asynchronously processes it for RAG ingestion."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    # Strip any directory components from the client-supplied filename before
    # using it in a path — otherwise a name like "../../../etc/passwd" would
    # let an upload write anywhere the backend process can reach.
    safe_filename = os.path.basename(file.filename)
    if not safe_filename or safe_filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    ext = safe_filename.split(".")[-1].lower() if "." in safe_filename else "txt"
    file_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_filename}"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Save to SQLite
    doc = UserDocument(
        conversation_id=conversation_id,
        filename=file.filename,
        file_path=str(file_path),
        file_type=ext,
        status="processing"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Deliberately does NOT create a chat message here. Uploading just starts
    # ingestion in the background (below) — the file stays a *pending*
    # attachment in the composer until the user actually hits send (Claude-
    # style: attach a file, optionally add text, send when ready). The real
    # chat message (with this document referenced in its attachments) gets
    # created when that send happens, over the websocket — see
    # app.api.websocket's message handler and app.core.agents.chat's
    # _handle_idle(attachments=...).

    # An image is a special case when a vision model is active: it'll be
    # sent to the model directly as real vision input at send-time (see
    # BaseAgent._attach_vision_images), so running OCR/RAG on it here would
    # be pure wasted latency and compute for content nothing will ever read.
    # Skip ingestion entirely rather than just not waiting on it — this
    # image's content is only ever available live in-context on the turn
    # it's attached, not indexed for later search, which is the deliberate
    # trade-off of going all-in on vision over OCR for these images.
    from app.db.crud import get_active_vision_mmproj_path
    skip_ocr = ext in ("png", "jpg", "jpeg") and get_active_vision_mmproj_path(db) is not None

    if skip_ocr:
        doc.status = "ready"
        db.commit()
        logger.info(f"Skipping OCR for image upload '{file.filename}' — vision model active, will be sent as real image input instead.")
    else:
        logger.info(f"Received document upload: {file.filename} -> starting background RAG ingestion.")
        # Process asynchronously via BackgroundTasks to immediately return HTTP 200
        background_tasks.add_task(
            async_process_upload_task,
            doc_id=doc.id,
            file_path=doc.file_path,
            file_type=doc.file_type,
            filename=doc.filename,
            conversation_id=conversation_id
        )

    return {
        "message": "Upload successful and processing started.",
        "document_id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
    }

@router.get("")
async def list_documents(conversation_id: Optional[str] = None, db = Depends(get_db)):
    """
    List documents, newest first. Pass conversation_id to scope to one chat —
    used by the chat view to resolve each attachment chip's live status
    (processing/ready/failed).
    """
    query = db.query(UserDocument)
    if conversation_id:
        query = query.filter(UserDocument.conversation_id == conversation_id)
    docs = query.order_by(UserDocument.created_at.desc()).all()
    return [{
        "id": d.id,
        "filename": d.filename,
        "status": d.status,
        "type": d.file_type,
        "conversation_id": d.conversation_id,
        "error_message": d.error_message,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d in docs]


@router.delete("/{document_id}")
async def delete_document(document_id: int, db = Depends(get_db)):
    """Delete an uploaded document: its file on disk, its indexed Qdrant chunks, and its DB row."""
    doc = db.query(UserDocument).filter(UserDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    file_path = Path(doc.file_path)
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError as e:
            logger.warning(f"Could not remove file {file_path} for document {document_id}: {e}")

    try:
        delete_document_points(document_id)
    except Exception as e:
        logger.warning(f"Could not remove Qdrant points for document {document_id}: {e}")

    db.delete(doc)
    db.commit()
    return {"success": True}
