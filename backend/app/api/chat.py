from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.db.crud import get_all_sessions, delete_chat_session

router = APIRouter(prefix="/api/chat", tags=["Chat History"])

@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    """Returns a list of all chat sessions with their previews and metadata."""
    return get_all_sessions(db)

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    """Deletes all messages for a given session."""
    deleted_count = delete_chat_session(db, session_id)
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Session not found or already empty.")
    return {"deleted": deleted_count, "session_id": session_id}

