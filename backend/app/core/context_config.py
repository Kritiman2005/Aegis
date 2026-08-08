"""
context_config.py — SQLite-backed context window configuration store.

All 4 agents (Chat, Planner, Executor, Extractor) read their tunable
parameters from this module at request time, so frontend changes take
effect immediately without restarting the backend.
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
    },
    "planner": {
        "max_history_messages": 6,
        "max_msg_chars": 2000,
        "max_result_snippet": 2000,
    },
    "executor": {
        # Executor is intentionally stateless — no history, schema-only.
        # This block is informational only; no tunable parameters.
        "description": "Executor is isolated by design. It receives only the tool schema for the current step and no chat history, ensuring deterministic JSON argument generation."
    },
    "extractor": {
        "max_tokens": 1024,
    },
    "hardware": {
        # Number of threads in the LLM executor.
        # MUST remain 1 regardless of hardware backend (Metal/CUDA/CPU).
        # Consumer-grade local inference cannot safely or performantly run
        # two decode passes concurrently — serialization is required everywhere.
        "llm_max_workers": 1,
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
                "extractor": json.loads(settings.extractor_json),
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
                extractor_json=json.dumps(config.get("extractor", {})),
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

