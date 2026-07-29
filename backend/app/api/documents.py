from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from typing import List, Dict, Any
import os
import shutil
from pathlib import Path
from datetime import datetime

from app.db.database import get_db
from app.db.models import UserDocument
from app.core.rag.processor import ingest_document

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def process_upload_task(doc_id: int, file_path: str, file_type: str, filename: str):
    """Background task to extract, chunk, and embed document."""
    from app.db.database import SessionLocal
    db = SessionLocal()
    doc = db.query(UserDocument).filter(UserDocument.id == doc_id).first()
    if not doc:
        db.close()
        return
        
    try:
        # Call RAG processor
        ingest_document(doc_id, file_path, file_type, filename)
        doc.status = "ready"
        db.commit()
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)
        db.commit()
    finally:
        db.close()

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    conversation_id: str = Form(...),
    db = Depends(get_db)
):
    """Uploads a document and queues it for RAG ingestion."""
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
    
    # Trigger background ingestion (Dense + Sparse)
    background_tasks.add_task(
        process_upload_task,
        doc_id=doc.id,
        file_path=doc.file_path,
        file_type=doc.file_type,
        filename=doc.filename
    )
    
    return {"message": "Upload successful, processing started.", "document_id": doc.id}

@router.get("")
async def list_documents(conversation_id: str, db = Depends(get_db)):
    """List documents for a specific conversation."""
    docs = db.query(UserDocument).filter(UserDocument.conversation_id == conversation_id).all()
    return [{"id": d.id, "filename": d.filename, "status": d.status, "type": d.file_type} for d in docs]
