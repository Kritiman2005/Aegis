"""
context_config.py — SQLite-backed context window configuration store.

app.core.workflows.engine's chat-generation node reads the "chat" section's
tunable parameters at request time, so frontend changes take effect
immediately without restarting the backend. "hardware" (n_gpu_layers,
n_threads) is likewise read live by llm_manager. "planner" is still
written by WorkflowsView.tsx's per-node memory-settings panel (as
AgentConfig, via POST /api/context-config's `agent` field) but nothing
reads it back anymore — it was the old built-in Agent Mode pipeline's
config before that pipeline was replaced by user-designed workflows.
"executor" and "advanced" hold no real tunables (informational/placeholder
only).
"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ── Default configuration ─────────────────────────────────────────────────────

DEFAULTS: Dict[str, Any] = {
    "chat": {
        "max_history_messages": 20,
        "max_msg_chars": 4000,
        "max_rag_chunks": 5,
        "max_output_tokens": 5120,
        "max_result_snippet": 2000,
        # "" = use app.prompts.chat's built-in persona+instructions. Set by
        # the Workflows "Default Pipeline" LLM card / a future Settings UI.
        "system_prompt_override": "",
        # Mirrors app.core.rag.processor.chunk_text's own defaults — kept
        # here so ingest_document() can read a user-tunable value instead
        # of the function's hardcoded ones. Only affects documents
        # uploaded after the change; existing embeddings aren't redone.
        "chunk_size": 300,
        "chunk_overlap": 50,
    },
    "planner": {
        "max_history_messages": 6,
        "max_msg_chars": 2000,
        "max_result_snippet": 2000,
        "max_output_tokens": 5120,
    },
    "executor": {
        # Executor is intentionally stateless — no history, schema-only.
        # This block is informational only; no tunable parameters.
        "description": "Executor is isolated by design. It receives only the tool schema for the current step and no chat history, ensuring deterministic JSON argument generation."
    },
    "hardware": {
        # Number of threads in the LLM executor.
        # MUST remain 1 regardless of hardware backend (Metal/CUDA/CPU).
        # Consumer-grade local inference cannot safely or performantly run
        # two decode passes concurrently — serialization is required everywhere.
        "llm_max_workers": 1,
        # -1 = offload all layers to GPU (Metal/CUDA) when available. Without a
        # default here, a fresh install has no "n_gpu_layers" key at all until the
        # user opens and saves the Hardware settings panel once, so llm_manager
        # never passes n_gpu_layers to Llama() and llama-cpp-python silently falls
        # back to CPU-only inference.
        "n_gpu_layers": -1,
        "n_threads": 4,
    },
    "advanced": {
        # Advanced settings for Tier B
    }
}

# ── Public API ────────────────────────────────────────────────────────────────

def load() -> Dict[str, Any]:
    """Load current config from SQLite, falling back to defaults for missing keys."""
    try:
        from app.db.database import SessionLocal
        from app.db.crud import get_system_settings
        with SessionLocal() as db:
            settings = get_system_settings(db)
            
            stored = {
                "chat": json.loads(settings.chat_json),
                "planner": json.loads(settings.planner_json),
                "advanced": json.loads(settings.advanced_json),
                "hardware": json.loads(settings.hardware_json)
            }
            
            # Deep-merge: fill any missing keys with defaults
            merged = {}
            for agent, default_vals in DEFAULTS.items():
                merged[agent] = {**default_vals, **stored.get(agent, {})}
            return merged
    except Exception as e:
        logger.warning(f"[ContextConfig] Failed to read config from DB: {e}. Using defaults.")
    return dict(DEFAULTS)


def save(config: Dict[str, Any]) -> None:
    """Persist the given config dict to SQLite."""
    try:
        from app.db.database import SessionLocal
        from app.db.crud import update_system_settings
        with SessionLocal() as db:
            update_system_settings(
                db,
                chat_json=json.dumps(config.get("chat", {})),
                planner_json=json.dumps(config.get("planner", {})),
                advanced_json=json.dumps(config.get("advanced", {})),
                hardware_json=json.dumps(config.get("hardware", {}))
            )
        logger.info("[ContextConfig] Config saved to SQLite DB.")
    except Exception as e:
        logger.error(f"[ContextConfig] Failed to save config to DB: {e}")
        raise


def reset() -> Dict[str, Any]:
    """Reset all config to defaults and persist."""
    save(DEFAULTS)
    return dict(DEFAULTS)


def get(agent: str) -> Dict[str, Any]:
    """Convenience: load config and return just the section for one agent."""
    return load().get(agent, DEFAULTS.get(agent, {}))

