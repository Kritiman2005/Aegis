from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from typing import List, Dict, Any
import os
import shutil
from pathlib import Path
from datetime import datetime

from app.db.database import get_db
from app.db.models import UserDocument
from app.core.rag.processor import ingest_document
from app.core.connection_manager import manager as ws_manager
import anyio

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

import logging
logger = logging.getLogger(__name__)

def process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str):
    """Background task to extract, chunk, and embed document."""
    from app.db.database import SessionLocal
    db = SessionLocal()
    doc = db.query(UserDocument).filter(UserDocument.id == doc_id).first()
    if not doc:
        db.close()
        return
        
    logger.info(f"Starting RAG processing for document: {filename} (ID: {doc_id})")
    try:
        # Call RAG processor
        ingest_document(doc_id, file_path, file_type, filename)
        doc.status = "ready"
        db.commit()
        logger.info(f"Successfully processed and embedded document: {filename}")
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
        logger.error(f"Failed to process document {filename}: {e}")
    finally:
        db.close()

async def async_process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str, conversation_id: str):
    """Async wrapper to broadcast progress over WebSockets and run the heavy ML ingestion in a separate thread."""
    await ws_manager.broadcast_json({"type": "document_progress", "content": f"Ingesting {filename} (this may take a moment)..."})
    try:
        # Run blocking processing in a thread pool so we don't freeze FastAPI's async event loop
        await anyio.to_thread.run_sync(
            process_upload_task, doc_id, file_path, file_type, filename
        )
        await ws_manager.broadcast_json({"type": "document_progress", "content": f"✅ {filename} is ready for chat."})
    except Exception as e:
        await ws_manager.broadcast_json({"type": "document_progress", "content": f"❌ Error processing {filename}."})

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
        
    ext = file.filename.split(".")[-1].lower() if "." in file.filename else "txt"
    file_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    
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
    
    return {"message": "Upload successful and processing started.", "document_id": doc.id}

@router.get("")
async def list_documents(conversation_id: str, db = Depends(get_db)):
    """List documents for a specific conversation."""
    docs = db.query(UserDocument).filter(UserDocument.conversation_id == conversation_id).all()
    return [{"id": d.id, "filename": d.filename, "status": d.status, "type": d.file_type} for d in docs]
