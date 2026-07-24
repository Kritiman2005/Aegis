from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from app.db.database import get_db
from app.db import crud

router = APIRouter(prefix="/api/memories", tags=["Memories"])

class MemoryUpdate(BaseModel):
    label: Optional[str] = None
    data_json: Optional[str] = None

@router.get("/")
def get_all_memories(db: Session = Depends(get_db)):
    """Fetch all confirmed memories."""
    memories = crud.get_all_entities(db)
    return [
        {
            "id": m.id,
            "conversation_id": m.conversation_id,
            "label": m.label,
            "entity_type": m.entity_type,
            "entity_id": m.entity_id,
            "data_json": m.data_json,
            "created_at": m.created_at
        }
        for m in memories
    ]

@router.put("/{memory_id}")
def update_memory(memory_id: int, payload: MemoryUpdate, db: Session = Depends(get_db)):
    """Update a memory's label or data_json."""
    updated = crud.update_entity(db, memory_id, payload.label, payload.data_json)
    if not updated:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "success"}

@router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    """Delete a memory."""
    success = crud.delete_entity(db, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}
