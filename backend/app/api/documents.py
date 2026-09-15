from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from typing import List, Dict, Any, Optional
import hashlib
import hashlib
import mimetypes
import os
import threading
from pathlib import Path
from datetime import datetime

from app.db.database import get_db
from app.db.models import UserDocument
from app.core.rag.processor import ingest_document, delete_document_points
from app.core.connection_manager import manager as ws_manager
import anyio

router = APIRouter(prefix="/api/documents", tags=["Documents"])

# mimetypes.guess_type() consults the Windows registry as a supplement to
# its built-in table on that platform — on a machine where an extension was
# never associated with a MIME type (common for .pdf on a clean/minimal
# Windows install with no PDF software ever registered, or when other
# software has overwritten the association), it silently returns (None,
# None), which get_document_raw() below then falls back to
# "application/octet-stream" for. A browser <iframe> given
# application/octet-stream never invokes its native PDF/image viewer
# regardless of Content-Disposition: inline — it just shows nothing, which
# is exactly the blank-preview symptom this dict exists to prevent. Listed
# explicitly (bypassing the OS lookup entirely) for every format this app
# actually serves through /raw, so preview rendering can't depend on
# whatever happens to be in a given user's Windows registry.
_EXPLICIT_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _guess_media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _EXPLICIT_MIME_TYPES:
        return _EXPLICIT_MIME_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"

# mimetypes.guess_type() consults the Windows registry as a supplement to
# its built-in table on that platform — on a machine where an extension was
# never associated with a MIME type (common for .pdf on a clean/minimal
# Windows install with no PDF software ever registered, or when other
# software has overwritten the association), it silently returns (None,
# None), which get_document_raw() below then falls back to
# "application/octet-stream" for. A browser <iframe> given
# application/octet-stream never invokes its native PDF/image viewer
# regardless of Content-Disposition: inline — it just shows nothing, which
# is exactly the blank-preview symptom this dict exists to prevent. Listed
# explicitly (bypassing the OS lookup entirely) for every format this app
# actually serves through /raw, so preview rendering can't depend on
# whatever happens to be in a given user's Windows registry.
_EXPLICIT_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# A document's very first ingestion can legitimately need to download the
# embedding/reranker models (a few hundred MB total) if they weren't already
# warmed by main.py's startup preload — slow but survivable on a slow
# connection. What must NOT happen is an unbounded hang: on a network that
# silently drops packets instead of refusing them (common behind restrictive
# firewalls/proxies), a streamed HTTP download can stall indefinitely past
# any of huggingface_hub's own per-request timeouts, leaving a document
# stuck in "processing" forever with zero explanation. This ceiling
# guarantees ingestion always reaches ready/failed within a bounded time.
_INGEST_TIMEOUT_SECONDS = 180

# No cap existed at all before this — a user could upload an arbitrarily
# large file (disk/memory risk on its own), and it fed straight into
# extraction/chunking/embedding with no bound on how long that would take,
# which is exactly what could make an otherwise-legitimate huge PDF/DOCX
# collide with _INGEST_TIMEOUT_SECONDS above and get killed as if it were a
# hung network download. Checked incrementally while streaming to disk
# (not from a Content-Length header, which a client can omit or lie about)
# so an oversized upload is rejected before it's ever fully written.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOAD_COPY_CHUNK_SIZE = 1024 * 1024

# No cap existed at all before this — a user could upload an arbitrarily
# large file (disk/memory risk on its own), and it fed straight into
# extraction/chunking/embedding with no bound on how long that would take,
# which is exactly what could make an otherwise-legitimate huge PDF/DOCX
# collide with _INGEST_TIMEOUT_SECONDS above and get killed as if it were a
# hung network download. Checked incrementally while streaming to disk
# (not from a Content-Length header, which a client can omit or lie about)
# so an oversized upload is rejected before it's ever fully written.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOAD_COPY_CHUNK_SIZE = 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_data_dir = os.environ.get("AEGIS_DATA_DIR")
UPLOAD_DIR = Path(_data_dir) / "uploads" if _data_dir else BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

import logging
logger = logging.getLogger(__name__)


def _get_active_ingestion_workflow_id(conversation_id: Optional[str]) -> Optional[int]:
    """The workflow (if any) that should handle an upload made in
    `conversation_id` — scoped handler first, then the one GLOBAL handler,
    if any (Workflow.is_ingestion_handler — see app.api.workflows's
    /set-ingestion-handler and app.db.crud.get_active_ingestion_workflow).
    A fresh SessionLocal since this runs inside process_upload_task's own
    worker thread, not the request's session."""
    from app.db.database import SessionLocal
    from app.db.crud import get_active_ingestion_workflow
    db = SessionLocal()
    try:
        row = get_active_ingestion_workflow(db, conversation_id=conversation_id)
        return row.id if row else None
    finally:
        db.close()


def _ingestion_workflow_wants_images(conversation_id: Optional[str]) -> bool:
    """
    True only if the workflow that would handle an upload in this
    conversation (see _get_active_ingestion_workflow_id) has its
    "document_upload_trigger" node's data.includeImages explicitly set —
    an opt-in, since a connected workflow's own "llm" node decides for
    itself whether to actually look at the image (via
    engine.py's _attach_workflow_vision_image), independent of whatever
    model happens to be active for chat. False (the default) preserves
    the existing behavior below exactly: an image upload is handled by
    the vision-at-chat-send-time path, never the workflow system.
    """
    import json as _json
    from app.db.database import SessionLocal
    from app.db.crud import get_active_ingestion_workflow
    db = SessionLocal()
    try:
        row = get_active_ingestion_workflow(db, conversation_id=conversation_id)
        if not row:
            return False
        graph = _json.loads(row.graph_json)
        for node in graph.get("nodes", []):
            data = node.get("data", {})
            if data.get("kind") == "document_upload_trigger" and data.get("includeImages"):
                return True
        return False
    finally:
        db.close()


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
        # Run ingestion on a separate worker with a hard ceiling — a bad
        # network (see _INGEST_TIMEOUT_SECONDS' comment above) can make the
        # underlying HTTP download stall well past this call ever
        # returning, and a plain synchronous call here would have no way
        # to give up. Deliberately a raw daemon Thread, NOT
        # concurrent.futures.ThreadPoolExecutor: that class registers every
        # worker with an atexit hook that JOINS it (waits, unbounded) before
        # the interpreter is allowed to exit — confirmed against a real
        # stuck call, where it made the whole backend process un-killable
        # on normal shutdown even though this function itself returned
        # correctly. A daemon thread carries no such hook — a stuck one is
        # abandoned cleanly at process exit instead of blocking it. This
        # can't forcibly kill the stuck thread while the process stays
        # alive (not possible in Python), but it guarantees THIS document
        # always reaches ready/failed within the ceiling, which is what the
        # UI actually reads.
        result: Dict[str, Any] = {}

        def _run():
            try:
                active_workflow_id = _get_active_ingestion_workflow_id(doc.conversation_id)
                if active_workflow_id is not None:
                    # A workflow is connected as the ingestion handler (see
                    # app.api.workflows's /set-ingestion-handler) — run it
                    # instead of the built-in pipeline for this document.
                    # asyncio.run is safe here specifically because this
                    # closure already runs on its own dedicated worker
                    # Thread (see the comment below) with no existing event
                    # loop of its own.
                    import asyncio
                    from app.core.workflows.engine import run_ingestion_workflow
                    asyncio.run(run_ingestion_workflow(doc_id, file_path, filename, file_type))
                else:
                    ingest_document(doc_id, file_path, file_type, filename)
                result["ok"] = True
            except Exception as e:
                result["ok"] = False
                result["error"] = e

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=_INGEST_TIMEOUT_SECONDS)

        if worker.is_alive():
            raise TimeoutError(
                f"Processing took longer than {_INGEST_TIMEOUT_SECONDS}s — this usually means "
                "the local search models couldn't finish downloading (check your internet "
                "connection, or a restrictive network may be blocking the download). Try again "
                "once you have a stable connection."
            )
        if not result.get("ok"):
            raise result["error"]

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

# Audio/video upload+transcription was removed — faster-whisper transcription
# stays only for the composer's live mic button (app/core/transcription.py,
# app/api/voice.py), which is unaffected by this. Rejected explicitly here
# rather than left to fail downstream in extract_text(), so the user gets an
# immediate, clear reason instead of a document stuck in "processing" until
# ingestion gets to it.
_REJECTED_AUDIO_VIDEO_EXTENSIONS = {
    "mp3", "wav", "m4a", "ogg", "flac", "aac", "wma",
    "mp4", "mov", "mkv", "webm", "avi",
}


# Audio/video upload+transcription was removed — faster-whisper transcription
# stays only for the composer's live mic button (app/core/transcription.py,
# app/api/voice.py), which is unaffected by this. Rejected explicitly here
# rather than left to fail downstream in extract_text(), so the user gets an
# immediate, clear reason instead of a document stuck in "processing" until
# ingestion gets to it.
_REJECTED_AUDIO_VIDEO_EXTENSIONS = {
    "mp3", "wav", "m4a", "ogg", "flac", "aac", "wma",
    "mp4", "mov", "mkv", "webm", "avi",
}


async def async_process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str, conversation_id: str):
    """Async wrapper to broadcast progress over WebSockets and run the heavy ML ingestion in a separate thread."""
    await ws_manager.broadcast_json({"type": "document_progress", "content": f"Ingesting {filename} (this may take a moment)..."})
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
    if ext in _REJECTED_AUDIO_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Audio/video upload isn't supported — this app no longer transcribes uploaded audio or video files.",
        )
    file_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_filename}"

    total_bytes = 0
    hasher = hashlib.sha256()
    with open(file_path, "wb") as buffer:
        while True:
            chunk = await file.read(_UPLOAD_COPY_CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > _MAX_UPLOAD_BYTES:
                buffer.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large — the limit is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                )
            buffer.write(chunk)
            hasher.update(chunk)

    content_hash = hasher.hexdigest()

    # Re-uploading the exact same bytes into the same conversation (e.g. the
    # user attaches the same file again in a later message) would otherwise
    # re-run the full extract/chunk/embed pipeline and leave a duplicate set
    # of chunks sitting in Qdrant next to the original — same content,
    # doubled retrieval noise, wasted embedding compute. Only checked within
    # this conversation: hybrid_search filters by which documents belong to
    # it, so a real cross-conversation dedup would need points to be
    # shareable across documents, not just skipped on upload — out of scope
    # here. "failed" rows are excluded so a prior failed ingestion doesn't
    # block a genuine retry from actually re-attempting it.
    existing = (
        db.query(UserDocument)
        .filter(
            UserDocument.conversation_id == conversation_id,
            UserDocument.content_hash == content_hash,
            UserDocument.status.in_(["ready", "processing"]),
        )
        .first()
    )
    if existing:
        file_path.unlink(missing_ok=True)
        logger.info(
            f"Upload '{file.filename}' matches existing document {existing.id} "
            f"('{existing.filename}') in this conversation by content hash — reusing it, skipping re-ingestion."
        )
        return {
            "message": "This file is already attached in this conversation — reusing the existing copy.",
            "document_id": existing.id,
            "filename": existing.filename,
            "file_type": existing.file_type,
            "deduplicated": True,
        }

    # Save to SQLite
    doc = UserDocument(
        conversation_id=conversation_id,
        filename=file.filename,
        file_path=str(file_path),
        file_type=ext,
        status="processing",
        content_hash=content_hash,
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

    # Images never go through RAG ingestion at all by default — vision is
    # the only way their content is ever read. A vision-capable active
    # model gets the image as real vision input at send-time
    # (BaseAgent._attach_vision_images); with no vision model active, OCR
    # is no longer used as a fallback (it silently produced garbled/
    # unreliable text for a feature — "read this image" — users expect to
    # just work), so the upload fails immediately with an explicit reason
    # instead of quietly indexing OCR noise. The one way around this
    # default: a connected ingestion workflow can opt in to receiving
    # images too (document_upload_trigger's "Also trigger on image
    # uploads") — its own "llm" node decides for itself whether to look at
    # the image (engine.py's _attach_workflow_vision_image checks THAT
    # node's own picked model, not whatever's active for chat), so this
    # bypasses the vision-gate below entirely rather than depending on it.
    from app.db.crud import get_active_vision_mmproj_path
    is_image = ext in ("png", "jpg", "jpeg")
    has_vision = get_active_vision_mmproj_path(db) is not None
    workflow_wants_images = is_image and _ingestion_workflow_wants_images(conversation_id)

    if workflow_wants_images:
        logger.info(f"Received image upload: {file.filename} -> routing to connected ingestion workflow (opted into images).")
        background_tasks.add_task(
            async_process_upload_task,
            doc_id=doc.id,
            file_path=doc.file_path,
            file_type=doc.file_type,
            filename=doc.filename,
            conversation_id=conversation_id
        )
    elif is_image and has_vision:
        doc.status = "ready"
        # Flags this row as having NO searchable content at all — see
        # ChatAgent._get_document_context (chat.py), which checks this at
        # send time to catch the case where the active model has changed
        # (or lost its vision pairing) since this upload, so the image's
        # content isn't silently dropped with zero explanation.
        doc.ocr_skipped_for_vision = True
        db.commit()
        logger.info(f"Skipping OCR for image upload '{file.filename}' — vision model active, will be sent as real image input instead.")
    elif is_image and not has_vision:
        doc.status = "failed"
        doc.error_message = (
            "No vision-capable model is active — download and select a vision model "
            "(LLM panel) to read image content. OCR is no longer used as a fallback."
        )
        db.commit()
        logger.info(f"Rejecting image upload '{file.filename}' — no vision model active and OCR fallback has been removed.")
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


def _purge_document(db, doc: UserDocument) -> None:
    """
    Removes one document's file on disk and its indexed Qdrant chunks, and
    stages its DB row for deletion (caller commits — batched callers like
    delete_documents_for_conversation below delete several docs under one
    commit instead of one per row).
    """
    file_path = Path(doc.file_path)
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError as e:
            logger.warning(f"Could not remove file {file_path} for document {doc.id}: {e}")

    try:
        delete_document_points(doc.id)
    except Exception as e:
        logger.warning(f"Could not remove Qdrant points for document {doc.id}: {e}")

    db.delete(doc)


def delete_documents_for_conversation(db, conversation_id: str) -> int:
    """
    Sweeps up every document (file on disk, Qdrant chunks, DB row) that
    belongs to a conversation — called when the conversation itself is
    deleted (app/api/chat.py's delete_session). Without this, a deleted
    chat's uploaded documents kept their file on disk and their embeddings
    in Qdrant forever, unreachable by any conversation_id but never cleaned
    up — a storage leak that grows with every chat a user deletes.
    """
    docs = db.query(UserDocument).filter(UserDocument.conversation_id == conversation_id).all()
    for doc in docs:
        _purge_document(db, doc)
    if docs:
        db.commit()
    return len(docs)


@router.get("/{document_id}/raw")
async def get_document_raw(document_id: int, download: bool = False, db = Depends(get_db)):
    """
    Serves the original uploaded file as-is — used by the frontend preview
    modal for content a browser can render natively (PDF via <iframe>,
    images via <img>) and, with ?download=true, as the "Download" link for
    everything else.

    content_disposition_type defaults to "attachment" in Starlette's
    FileResponse whenever `filename` is passed — silently forcing every
    response into a download instead of rendering inline, which is why an
    <iframe src=".../raw"> would show nothing at all (the browser tries to
    download the framed content rather than display it). Explicit "inline"
    here is what actually makes the PDF preview work; ?download=true is a
    deliberate opt-in back to "attachment" for the real download link,
    since the frontend and backend are different origins (localhost:3000 vs
    127.0.0.1:8000) — the HTML `download` attribute on an <a> tag is only
    honored by browsers for same-origin (or blob/data) URLs, so it can't be
    relied on here to force the save behavior on its own.
    """
    doc = db.query(UserDocument).filter(UserDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="The file is no longer on disk.")
    return FileResponse(
        file_path,
        media_type=_guess_media_type(doc.filename),
        filename=doc.filename,
        content_disposition_type="attachment" if download else "inline",
    )


# Preview-only cap — independent of _MAX_EXTRACTED_CHARS in rag/processor.py
# (which bounds what gets chunked/embedded). This just keeps a single
# preview response from shipping megabytes of text to the browser at once;
# the full content is still what actually got indexed for chat/search.
_PREVIEW_MAX_CHARS = 50_000


@router.get("/{document_id}/text")
async def get_document_text(document_id: int, db = Depends(get_db)):
    """
    Extracts and returns a text preview for formats a browser can't render
    natively (DOCX/XLSX/PPTX/CSV/TXT/MD) — used by the frontend preview
    modal's fallback pane. Images and PDFs are previewed via /raw instead
    and never call this.
    """
    doc = db.query(UserDocument).filter(UserDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.status == "failed":
        raise HTTPException(status_code=422, detail=doc.error_message or "This document failed to process.")
    if doc.status != "ready":
        raise HTTPException(status_code=409, detail="This document is still processing.")

    from app.core.rag.processor import extract_text
    try:
        text = await anyio.to_thread.run_sync(extract_text, doc.file_path, doc.file_type)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    truncated = len(text) > _PREVIEW_MAX_CHARS
    return {
        "filename": doc.filename,
        "file_type": doc.file_type,
        "text": text[:_PREVIEW_MAX_CHARS],
        "truncated": truncated,
    }


@router.delete("/{document_id}")
async def delete_document(document_id: int, db = Depends(get_db)):
    """Delete an uploaded document: its file on disk, its indexed Qdrant chunks, and its DB row."""
    doc = db.query(UserDocument).filter(UserDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    _purge_document(db, doc)
    db.commit()
    return {"success": True}
