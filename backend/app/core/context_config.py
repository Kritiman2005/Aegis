"""
context_config.py — JSON-file-backed context window configuration store.

All 4 agents (Chat, Planner, Executor, Extractor) read their tunable
parameters from this module at request time, so frontend changes take
effect immediately without restarting the backend.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Store the config JSON next to main.py in the backend dir
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "context_config.json"

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
}

# ── Public API ────────────────────────────────────────────────────────────────

def load() -> Dict[str, Any]:
    """Load current config from disk, falling back to defaults for missing keys."""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            # Deep-merge: fill any missing keys with defaults
            merged = {}
            for agent, default_vals in DEFAULTS.items():
                merged[agent] = {**default_vals, **stored.get(agent, {})}
            return merged
    except Exception as e:
        logger.warning(f"[ContextConfig] Failed to read config file: {e}. Using defaults.")
    return dict(DEFAULTS)


def save(config: Dict[str, Any]) -> None:
    """Persist the given config dict to disk."""
    try:
        _CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"[ContextConfig] Config saved to {_CONFIG_PATH}")
    except Exception as e:
        logger.error(f"[ContextConfig] Failed to save config: {e}")
        raise


def reset() -> Dict[str, Any]:
    """Reset all config to defaults and persist."""
    save(DEFAULTS)
    return dict(DEFAULTS)


def get(agent: str) -> Dict[str, Any]:
    """Convenience: load config and return just the section for one agent."""
    return load().get(agent, DEFAULTS.get(agent, {}))
