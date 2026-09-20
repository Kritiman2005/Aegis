"""
Aegis — in-memory recent-log buffer for the Dependencies panel

The app currently only logs to stdout/stderr (main.py has no
logging.FileHandler) — fine for `python main.py` in a terminal, useless
for a packaged Electron app's end user, who has no terminal to read. This
gives the Dependencies panel a real "what's actually going wrong" view
without needing a whole log-file/rotation setup: a fixed-size ring buffer
attached to the ROOT logger, so every module's own logger.warning/error/
exception call (propagate=True by default, the standard logging behavior
nothing in this app overrides) ends up here too, with zero per-module
wiring. WARNING level and up only — INFO-level noise (every HTTP request
uvicorn logs) would drown out the actually-useful signal a user is trying
to read: "why did the thing I just clicked fail?"

Every module in this app logs through the ROOT logger, so without a filter
this buffer mixes install failures, OAuth token errors, and RAM warnings
about the LLM into one firehose — none of which is "why couldn't I install
that package", which is the only thing the Dependencies panel is actually
about. Each entry is tagged with a `category`, inferred from the logger's
own dotted name (record.name — e.g. "app.core.optional_deps" -> "dependency",
"app.core.workflows.engine" -> "workflow"), so callers can filter to just
the category their own panel cares about instead of showing everything.

install() must be called once, as early as possible in main.py (before
uvicorn/any app.api.* router imports) — so it's already attached by the
time anything interesting could go wrong.
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_MAX_ENTRIES = 500
_buffer: "deque[Dict[str, Any]]" = deque(maxlen=_MAX_ENTRIES)

# Ordered so the first matching prefix wins — checked most-specific-first
# isn't required here since none of these prefixes overlap with each other.
_CATEGORY_PREFIXES = {
    "app.core.optional_deps": "dependency",
    "app.api.optional_deps": "dependency",
    "app.core.workflows": "workflow",
    "app.api.workflows": "workflow",
    "app.core.agents.chat": "chat",
    "app.api.websocket": "chat",
    "app.mcp": "connector",
    "app.api.connectors": "connector",
    "app.auth": "account",
    "app.db.crud": "account",
}


def _category_for(logger_name: str) -> str:
    for prefix, category in _CATEGORY_PREFIXES.items():
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return category
    return "other"


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "category": _category_for(record.name),
                "message": self.format(record),
            })
        except Exception:
            pass  # logging itself must never be what crashes the app


def install() -> None:
    """Idempotent — safe to call more than once. pip's own programmatic
    install (pip._internal.cli.main.main, used by app.core.optional_deps for
    the Dependencies panel's install box) calls its own setup_logging()
    internally, which runs logging.config.dictConfig() with a "root" section
    — this REPLACES the root logger's entire handler list with pip's own
    console handlers, silently evicting this handler the first time any
    package is installed. optional_deps.install_custom_sync calls this again
    right after every pip invocation to restore it; the isinstance guard
    here stops that from ever stacking up duplicate handlers (which would
    otherwise double-log everything from the second install onward)."""
    root = logging.getLogger()
    if any(isinstance(h, _RingBufferHandler) for h in root.handlers):
        return
    handler = _RingBufferHandler()
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)


def get_recent(limit: int = 200, category: Optional[str] = None) -> List[Dict[str, Any]]:
    entries = list(_buffer)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries[-limit:]


def clear() -> None:
    _buffer.clear()
