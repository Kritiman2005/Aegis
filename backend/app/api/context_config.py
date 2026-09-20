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
    max_output_tokens: int = Field(..., ge=64, le=128000, description="Max tokens the LLM may generate")
    max_result_snippet: int = Field(2000, ge=100, le=10000, description="Character cap for recent tool result snippets")
    system_prompt_override: Optional[str] = Field(None, max_length=8000, description="Full replacement for Chat Mode's system prompt; empty/unset = built-in default")
    chunk_size: int = Field(300, ge=50, le=2000, description="Word count per document chunk on ingest")
    chunk_overlap: int = Field(50, ge=0, le=500, description="Word overlap between consecutive chunks on ingest")

class AgentConfig(BaseModel):
    max_history_messages: int = Field(..., ge=1, le=20, description="Max chat turns passed to Planner LLM")
    max_msg_chars: int = Field(..., ge=100, le=10000, description="Character cap per message in history")
    max_result_snippet: int = Field(..., ge=100, le=10000, description="Character cap for recent tool result snippets")
    max_output_tokens: int = Field(5120, ge=64, le=128000, description="Max tokens the LLM may generate")

class AdvancedConfig(BaseModel):
    # Tier B placeholders
    rag_confidence_threshold: float = Field(0.1, description="RAG search score threshold")
    
class HardwareConfig(BaseModel):
    n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU")
    n_threads: int = Field(4, description="Number of CPU threads to use")
    # Note: db_max_workers dropped per UI discussion, llm_max_workers locked.
    # Context window cap lives per-model (ModelRegistry.context_cap, see
    # POST /api/hub/{model_id}/context-cap in models_hub.py) — different
    # models legitimately want different caps, so it's not a single
    # app-wide hardware setting like n_gpu_layers/n_threads are.

class ContextConfigPayload(BaseModel):
    chat: Optional[ChatConfig] = None
    agent: Optional[AgentConfig] = None
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
    if payload.agent is not None:
        current["planner"].update(payload.agent.model_dump(exclude_unset=True))
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

    # Clear is_active so /api/hardware/status stops reporting this model as
    # active via its "nothing loaded, fall back to the DB's active row" path —
    # otherwise ejecting looks like a no-op in the UI even though the model
    # really was freed from RAM.
    from app.db.database import SessionLocal
    from app.db.models import ModelRegistry
    with SessionLocal() as db:
        db.query(ModelRegistry).update({ModelRegistry.is_active: False})
        db.commit()

    return {"success": True, "message": f"Unloaded {len(loaded)} model(s)."}


@router.post("/api/hardware/unload/{model_id}")
def unload_one_model(model_id: int):
    """
    Ejects exactly ONE model from RAM — the per-model 'Eject' button on
    each Memory Hub row. Unlike /api/hardware/unload (every loaded model at
    once), this leaves every OTHER model — including one a workflow loaded
    independently (see context_config.get_hardware_status's
    other_loaded_models) — untouched, so ejecting one model a workflow
    isn't using doesn't also kill a different one it is.
    """
    if is_llm_busy():
        raise HTTPException(
            status_code=409,
            detail="Cannot unload model while a generation is in progress."
        )

    from app.db.database import SessionLocal
    from app.db.models import ModelRegistry
    from app.core.agents.chat import get_llm_manager

    with SessionLocal() as db:
        model = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="Model not found.")

        manager = get_llm_manager()
        was_loaded = manager.unload_model(model.name)

        # Same reasoning as the all-models endpoint above: clear is_active
        # so /api/hardware/status stops reporting this exact model as
        # active via its DB-fallback path once it's the one just ejected.
        if model.is_active:
            model.is_active = False
            db.commit()

        # Captured before the session closes below — accessing an ORM
        # attribute on a detached instance after the `with` block exits
        # raises DetachedInstanceError.
        display_name = model.display_name

    return {"success": True, "was_loaded": was_loaded, "message": f"Ejected '{display_name}' from RAM."}


class LoadModelRequest(BaseModel):
    model_id: int

@router.post("/api/hardware/load")
def load_active_model(req: LoadModelRequest):
    if is_llm_busy():
        raise HTTPException(
            status_code=409, 
            detail="Cannot switch model while a generation is in progress."
        )
    
    from app.db.database import SessionLocal
    from app.db.models import ModelRegistry
    from app.core.agents.chat import get_llm_manager
    
    with SessionLocal() as db:
        model = db.query(ModelRegistry).filter(ModelRegistry.id == req.model_id).first()
        if not model or model.status != "downloaded":
            raise HTTPException(status_code=404, detail="Model not found or not downloaded.")

        db.query(ModelRegistry).update({ModelRegistry.is_active: False})
        model.is_active = True
        db.commit()
        model_name = model.name

    manager = get_llm_manager()
    loaded = list(manager.loaded_models.keys())
    for name in loaded:
        manager.unload_model(name)

    # Load the newly-active model in the background right away, so the switch
    # is already warm by the time you send a message instead of lazily loading
    # (and stalling) on that first chat request.
    from app.core.agents.chat import llm_executor
    llm_executor.submit(lambda: manager.get_model(model_name))


    return {"success": True, "message": f"Set active model successfully."}

@router.get("/api/hardware/status")
def get_hardware_status():
    """Return active model, system RAM usage, and model capabilities for the frontend UI."""
    from app.core.agents.chat import get_llm_manager
    from app.core.llm_manager import resolve_effective_n_ctx
    from app.db.crud import get_model_usage_by_workflows
    import psutil
    manager = get_llm_manager()
    loaded_models = list(manager.loaded_models.keys())

    active_model = "None"
    active_model_display = "None"
    max_context = 4096
    other_loaded_models = []

    from app.db.database import SessionLocal
    from app.db.models import ModelRegistry
    with SessionLocal() as db:
        if loaded_models:
            active_model = loaded_models[0]
            # Try to fetch capabilities for the loaded model
            model_info = db.query(ModelRegistry).filter(ModelRegistry.name == active_model).first()
            if not model_info:
                model_info = db.query(ModelRegistry).filter(ModelRegistry.repo_id == active_model).first()
            if model_info:
                active_model_display = model_info.display_name
            else:
                active_model_display = active_model
            # Ground truth from the actual loaded llama.cpp instance — not
            # the DB's native/trained context_length, which is frequently
            # much higher than what the model was really loaded with (see
            # llm_manager.resolve_effective_n_ctx).
            try:
                max_context = manager.loaded_models[active_model].n_ctx()
            except Exception:
                max_context = resolve_effective_n_ctx(
                    model_info.context_length if model_info else None,
                    model_info.name if model_info else None,
                )

            # Other models a workflow node (or anything else) has loaded
            # alongside the "primary" active one — loaded_models isn't
            # exclusive, so these were previously invisible to the UI even
            # though they're really sitting in RAM right now.
            usage_by_model = get_model_usage_by_workflows(db)
            for name in loaded_models:
                if name == active_model:
                    continue
                info = db.query(ModelRegistry).filter(ModelRegistry.name == name).first()
                try:
                    real_ctx = manager.loaded_models[name].n_ctx()
                except Exception:
                    real_ctx = resolve_effective_n_ctx(info.context_length if info else None, name)
                other_loaded_models.append({
                    "name": name,
                    "display_name": (info.display_name if info else name),
                    "max_context": real_ctx,
                    "used_by_workflows": usage_by_model.get(name, []),
                })
        else:
            # Only trust an explicit is_active flag here — silently falling back to
            # "any downloaded model" would make the UI claim a model is active right
            # after the user explicitly ejected it (is_active gets cleared on eject,
            # but a leftover downloaded row would otherwise still get picked and
            # displayed as if it were active/loaded).
            active = db.query(ModelRegistry).filter(ModelRegistry.is_active == True).first()
            if active:
                active_model = active.repo_id or active.name
                active_model_display = active.display_name
                max_context = resolve_effective_n_ctx(active.context_length, active.name)

    mem = psutil.virtual_memory()
    total_gb = mem.total / (1024**3)
    used_gb = mem.used / (1024**3)

    return {
        "active_model": active_model,
        "active_model_display": active_model_display,
        "max_context": max_context,
        "other_loaded_models": other_loaded_models,
        "ram_total_gb": round(total_gb, 1),
        "ram_used_gb": round(used_gb, 1),
        "ram_percent": mem.percent
    }


# ─── Hardware detection (first-run "Your Mac is ready" screen) ────────────────

def _sysctl(key: str) -> Optional[str]:
    """macOS only — returns None (never raises) on any other platform or failure."""
    try:
        import subprocess
        out = subprocess.check_output(["sysctl", "-n", key], stderr=subprocess.DEVNULL, timeout=2)
        return out.decode().strip() or None
    except Exception:
        return None


def _detect_gpu_backend(is_apple_silicon: bool) -> str:
    """Best-effort, never fabricated: only claims a backend we can actually confirm."""
    if is_apple_silicon:
        return "Metal"
    try:
        import subprocess
        subprocess.check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], stderr=subprocess.DEVNULL, timeout=2)
        return "CUDA"
    except Exception:
        return "CPU"


@router.get("/api/hardware/detect")
def detect_hardware():
    """
    Real, honestly-detected machine capabilities for the one-time "Your Mac
    is ready" welcome screen — no fabricated specs. Apple Silicon gets the
    full breakdown (chip name, performance/efficiency core split) since
    sysctl exposes it directly; other platforms get an honest subset.
    """
    import os
    import platform as _platform
    import psutil

    system = _platform.system()
    is_apple_silicon = system == "Darwin" and _platform.machine() == "arm64"

    chip_name = _sysctl("machdep.cpu.brand_string") if system == "Darwin" else None
    perf_raw = _sysctl("hw.perflevel0.logicalcpu") if is_apple_silicon else None
    eff_raw = _sysctl("hw.perflevel1.logicalcpu") if is_apple_silicon else None

    hw_cfg = cfg_store.load().get("hardware", {})

    return {
        "platform": system,
        "is_apple_silicon": is_apple_silicon,
        "chip_name": chip_name,
        "total_cores": os.cpu_count() or 0,
        "performance_cores": int(perf_raw) if perf_raw and perf_raw.isdigit() else None,
        "efficiency_cores": int(eff_raw) if eff_raw and eff_raw.isdigit() else None,
        "gpu_backend": _detect_gpu_backend(is_apple_silicon),
        "ram_total_gb": round(psutil.virtual_memory().total / (1024 ** 3), 1),
        "gpu_offload_layers": hw_cfg.get("n_gpu_layers", -1),
    }


# ─── One-time onboarding state ─────────────────────────────────────────────────

@router.get("/api/onboarding/status")
def get_onboarding_status():
    from app.db.models import OnboardingState
    with SessionLocal() as db:
        row = db.query(OnboardingState).filter(OnboardingState.id == 1).first()
        return {"welcome_seen": bool(row.welcome_seen) if row else False}


@router.post("/api/onboarding/welcome-seen")
def mark_welcome_seen():
    from app.db.models import OnboardingState
    with SessionLocal() as db:
        row = db.query(OnboardingState).filter(OnboardingState.id == 1).first()
        if row:
            row.welcome_seen = True
        else:
            db.add(OnboardingState(id=1, welcome_seen=True))
        db.commit()
    return {"status": "ok"}
