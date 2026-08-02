"""
context_config.py — API routes for reading and writing agent context window config.

GET  /api/context-config        → returns current config for all 4 agents
POST /api/context-config        → accepts updated config, validates, saves
POST /api/context-config/reset  → resets all config to defaults
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from app.core import context_config as cfg_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class ChatConfig(BaseModel):
    max_history_messages: int = Field(..., ge=1, le=100, description="Max chat turns passed to Chat LLM")
    max_msg_chars: int = Field(..., ge=100, le=20000, description="Character cap per message in history")
    max_rag_chunks: int = Field(..., ge=0, le=20, description="Number of RAG document chunks to inject")


class PlannerConfig(BaseModel):
    max_history_messages: int = Field(..., ge=1, le=20, description="Max chat turns passed to Planner LLM")
    max_msg_chars: int = Field(..., ge=100, le=10000, description="Character cap per message in history")
    max_result_snippet: int = Field(..., ge=100, le=10000, description="Character cap for recent tool result snippets")


class ExtractorConfig(BaseModel):
    max_tokens: int = Field(..., ge=64, le=4096, description="Max tokens the Extractor LLM may generate")


class ContextConfigPayload(BaseModel):
    chat: Optional[ChatConfig] = None
    planner: Optional[PlannerConfig] = None
    extractor: Optional[ExtractorConfig] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/api/context-config")
def get_context_config():
    """Return the current context window configuration for all agents."""
    return cfg_store.load()


@router.post("/api/context-config")
def update_context_config(payload: ContextConfigPayload):
    """
    Merge the supplied values into the current config and persist.
    Only the provided agent sections are updated; the rest are unchanged.
    """
    current = cfg_store.load()

    if payload.chat is not None:
        current["chat"].update(payload.chat.model_dump())
    if payload.planner is not None:
        current["planner"].update(payload.planner.model_dump())
    if payload.extractor is not None:
        current["extractor"].update(payload.extractor.model_dump())

    try:
        cfg_store.save(current)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

    logger.info("[ContextConfig API] Config updated successfully.")
    return {"success": True, "config": current}


@router.post("/api/context-config/reset")
def reset_context_config():
    """Reset all agent context config to factory defaults."""
    defaults = cfg_store.reset()
    return {"success": True, "config": defaults}
