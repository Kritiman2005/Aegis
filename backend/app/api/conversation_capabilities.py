"""
Aegis — Per-conversation Tool/Skill activation

Backs the chat composer's "+" menu Tools/Skills toggles (Claude Desktop-
style): installed once globally via the Marketplace, then switched on/off
per conversation. See db.models.ConversationDisabledCapability for the
storage model (absence of a row = active).
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.database import SessionLocal
from app.db.crud import is_capability_active, set_capability_active
from app.core.marketplace import list_tools
from app.core.skills import load_skills

router = APIRouter(prefix="/api/conversations", tags=["Conversation Capabilities"])


@router.get("/{conversation_id}/capabilities")
def get_capabilities(conversation_id: str):
    db = SessionLocal()
    try:
        tools = []
        for tool in list_tools():
            if not tool["installed"]:
                continue
            tools.append({
                "id": tool["id"],
                "name": tool["name"],
                "active": is_capability_active(db, conversation_id, "tool", tool["id"]),
            })

        skills = []
        for skill in load_skills():
            skills.append({
                "id": skill.folder,
                "name": skill.name,
                "description": skill.description,
                "active": is_capability_active(db, conversation_id, "skill", skill.folder),
            })

        return {"tools": tools, "skills": skills}
    finally:
        db.close()


class ToggleRequest(BaseModel):
    active: bool


@router.post("/{conversation_id}/capabilities/{capability_type}/{capability_id}")
def toggle_capability(conversation_id: str, capability_type: str, capability_id: str, req: ToggleRequest):
    if capability_type not in ("tool", "skill"):
        return {"error": "capability_type must be 'tool' or 'skill'"}
    db = SessionLocal()
    try:
        set_capability_active(db, conversation_id, capability_type, capability_id, req.active)
    finally:
        db.close()
    return {"status": "ok"}
