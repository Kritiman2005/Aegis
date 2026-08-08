"""
context_config.py — API routes for reading and writing agent context window config.

GET  /api/context-config        → returns current config for all 4 agents
POST /api/context-config        → accepts updated config, validates, saves
POST /api/context-config/reset  → resets all config to defaults
POST /api/hardware/unload       → explicitly unloads a model from RAM
"""

import json
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
import concurrent.futures

from app.core import context_config as cfg_store
from app.core.agents.chat import llm_executor
from app.db.database import SessionLocal
from app.db.crud import log_setting_change

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

class AdvancedConfig(BaseModel):
    # Tier B placeholders
    rag_confidence_threshold: float = Field(0.1, description="RAG search score threshold")
    
class HardwareConfig(BaseModel):
    n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU")
    n_threads: int = Field(4, description="Number of CPU threads to use")
    # Note: db_max_workers dropped per UI discussion, llm_max_workers locked.

class ContextConfigPayload(BaseModel):
    chat: Optional[ChatConfig] = None
    planner: Optional[PlannerConfig] = None
    extractor: Optional[ExtractorConfig] = None
    advanced: Optional[AdvancedConfig] = None
    hardware: Optional[HardwareConfig] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def is_llm_busy() -> bool:
    """Checks if the single-threaded LLM executor is currently busy by attempting a 0-timeout dummy task."""
    try:
        fut = llm_executor.submit(lambda: True)
        # If the pool is idle, it executes almost instantly. If busy, it will block.
        fut.result(timeout=0.01)
        return False
    except concurrent.futures.TimeoutError:
        return True
    except Exception:
        return False


def _reload_model_bg():
    """Background task to unload and optionally reload the model with new hardware config."""
    from app.core.agents.chat import get_llm_manager
    manager = get_llm_manager()
    # Unloading forces GC. Next time the app requests the model, it will load with the new config.
    # Alternatively we can preload it here. We'll just unload so it frees RAM immediately.
    logger.info("Hardware config changed. Unloading all models to apply new settings.")
    loaded_names = list(manager.loaded_models.keys())
    for name in loaded_names:
        manager.unload_model(name)

def _log_changes(db, current: dict, incoming: dict):
    """Compares dictionaries and logs telemetry for any changes."""
    for section_name, section_vals in incoming.items():
        if not section_vals: continue
        current_section = current.get(section_name, {})
        for key, new_val in section_vals.items():
            old_val = current_section.get(key)
            if old_val != new_val:
                log_setting_change(db, f"{section_name}.{key}", str(old_val), str(new_val))

# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/api/context-config")
def get_context_config():
    """Return the current context window configuration for all agents."""
    return cfg_store.load()


@router.post("/api/context-config")
def update_context_config(payload: ContextConfigPayload, bg_tasks: BackgroundTasks):
    """
    Merge the supplied values into the current config and persist.
    If hardware (n_gpu_layers, n_threads) changes, check if LLM is busy (409 if so)
    and then trigger a model reload.
    """
    current = cfg_store.load()
    
    # Check if hardware changed
    hardware_changed = False
    if payload.hardware is not None:
        new_hw = payload.hardware.model_dump()
        old_hw = current.get("hardware", {})
        if new_hw.get("n_gpu_layers") != old_hw.get("n_gpu_layers") or \
           new_hw.get("n_threads") != old_hw.get("n_threads"):
            hardware_changed = True

    # If hardware changed, explicitly block if generation is in progress
    if hardware_changed:
        if is_llm_busy():
            raise HTTPException(
                status_code=409, 
                detail="Cannot change hardware settings while a generation is in progress. Please wait."
            )

    # Log changes to telemetry DB
    try:
        with SessionLocal() as db:
            _log_changes(db, current, payload.model_dump(exclude_unset=True))
    except Exception as e:
        logger.error(f"Failed to log setting changes: {e}")

    # Apply updates
    if payload.chat is not None:
        current["chat"].update(payload.chat.model_dump(exclude_unset=True))
    if payload.planner is not None:
        current["planner"].update(payload.planner.model_dump(exclude_unset=True))
    if payload.extractor is not None:
        current["extractor"].update(payload.extractor.model_dump(exclude_unset=True))
    if payload.advanced is not None:
        current["advanced"].update(payload.advanced.model_dump(exclude_unset=True))
    if payload.hardware is not None:
        current["hardware"].update(payload.hardware.model_dump(exclude_unset=True))

    try:
        cfg_store.save(current)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

    if hardware_changed:
        bg_tasks.add_task(_reload_model_bg)

    logger.info("[ContextConfig API] Config updated successfully.")
    return {"success": True, "config": current}


@router.post("/api/context-config/reset")
def reset_context_config():
    """Reset all agent context config to factory defaults."""
    defaults = cfg_store.reset()
    return {"success": True, "config": defaults}

@router.post("/api/hardware/unload")
def unload_model():
    """Explicit endpoint triggered by 'Manual Unload Model' button."""
    if is_llm_busy():
        raise HTTPException(
            status_code=409, 
            detail="Cannot unload model while a generation is in progress."
        )
    
    from app.core.agents.chat import get_llm_manager
    manager = get_llm_manager()
    loaded = list(manager.loaded_models.keys())
    for name in loaded:
        manager.unload_model(name)
        
    return {"success": True, "message": f"Unloaded {len(loaded)} model(s)."}

