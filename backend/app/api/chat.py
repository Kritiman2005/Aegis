from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.db.crud import get_all_sessions, delete_chat_session
from app.api.documents import delete_documents_for_conversation

router = APIRouter(prefix="/api/chat", tags=["Chat History"])

@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    """Returns a list of all chat sessions with their previews and metadata."""
    return get_all_sessions(db)

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    """
    Deletes all messages for a given session, and any documents uploaded
    into it (file on disk, Qdrant embeddings, DB row) — without the second
    part, a deleted chat's uploaded documents were never cleaned up: no
    conversation_id could ever reach them again, but they stayed on disk
    and in Qdrant indefinitely.
    """
    deleted_count = delete_chat_session(db, session_id)
    documents_deleted = delete_documents_for_conversation(db, session_id)
    if deleted_count == 0 and documents_deleted == 0:
        raise HTTPException(status_code=404, detail="Session not found or already empty.")
    return {"deleted": deleted_count, "documents_deleted": documents_deleted, "session_id": session_id}

