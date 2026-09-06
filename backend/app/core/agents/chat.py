import json
import logging
import re
from typing import Dict, List, Optional, AsyncGenerator, Any
import anyio

from app.core.llm_manager import LLMManager
from app.db.database import SessionLocal
from app.db.crud import save_entity, build_entity_context_block

from .base import BaseAgent
from .planner import PlannerAgent
from .executor import ExecutorAgent
from app.prompts.chat import build_chat_prompt

logger = logging.getLogger(__name__)

# Lazy initialization of LLM manager to prevent DB queries at import time
_llm_manager = None

import concurrent.futures

# Read thread pool sizes from context_config. Falls back to safe hardcoded defaults
# if the config file is missing (e.g. fresh install) or unreadable — ensuring the
# app always starts even before any settings have been saved.
try:
    from app.core import context_config as _ctx_cfg_hw
    _hw = _ctx_cfg_hw.get("hardware")
except Exception:
    _hw = {}

# Single-thread executor for all LLM calls.
# Consumer-grade local hardware (Metal, CUDA, CPU) cannot safely or
# performantly run two concurrent decode passes regardless of backend:
# Metal hard-crashes; CUDA degrades from VRAM/KV-cache contention;
# CPU starves both calls of cores. Serialization is required everywhere.
# DEFAULT: 1. This value should NOT be changed without explicit testing.
_llm_workers = int(_hw.get("llm_max_workers", 1))
if _llm_workers != 1:
    logger.warning(
        f"[ThreadPool] llm_max_workers={_llm_workers} — overriding to 1. "
        "Multiple LLM workers are unsafe on consumer hardware."
    )
    _llm_workers = 1
llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_llm_workers)

# Dedicated thread pool for SQLite DB operations — decoupled from the LLM lane
# so a slow disk write never blocks inference. 2 workers is safe for SQLite
# in WAL mode (concurrent readers, serialized writers).
_db_workers = int(_hw.get("db_max_workers", 2))
db_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_db_workers)

def get_llm_manager():
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager


# Detects a chat answer (_call_llm_text) that's degenerated into a bare JSON
# object/array instead of prose — verified live against this app's own
# bundled model: a grammar constraining just the first character wasn't
# enough (the model spent that one character on a throwaway space, then
# emitted the exact same JSON right after); forcing a longer non-brace
# prefix just made it pad with incoherent filler *before* the same JSON,
# which is worse, not better. Grammar constraints only mask which tokens
# are legal — they can't make the model want to write prose instead. This
# is a semantic check on the finished text instead: strict JSON.loads,
# not just "starts with a brace", so prose that happens to mention one
# (an inline code example) is never a false positive.
def _looks_like_pure_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, (dict, list))


def _extract_text_from_json_leak(text: str) -> Optional[str]:
    """
    Last-resort salvage when a chat answer leaked as JSON even after one
    corrective regeneration attempt (see _call_llm_text): recursively pull
    the first reasonably long string value out of the structure rather
    than showing the user raw braces. Returns None if nothing usable is
    found, so the caller can fall back to a plain clarification message.
    """
    try:
        parsed = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None

    def _walk(node):
        if isinstance(node, str):
            return node if len(node.strip()) >= 15 else None
        if isinstance(node, dict):
            for value in node.values():
                found = _walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found:
                    return found
        return None

    return _walk(parsed)


def _sanitize_one_shot_text(text: str, fallback: str) -> str:
    """
    Belt-and-suspenders cleanup for a single already-generated LLM text
    field that reaches the user with no chance to regenerate — the
    planner's "direct_response" and "clarifying_question" (chat.py's
    _handle_idle, Agent Mode branch). Both are schema-typed as strings by
    the plan grammar, so they can't literally BE a nested JSON object —
    but nothing stops the model from writing a JSON-shaped blob AS the
    string's content (the same shared-history-imitation failure mode
    _call_llm_text guards against for Chat Mode, just narrower here).
    Unlike _call_llm_text this never regenerates: a full plan-generation
    call is too expensive to redo just to fix one string field, and this
    path doesn't stream live, so there's no partial output to protect
    either. Straight extract-or-fallback instead.
    """
    if not text or not _looks_like_pure_json(text):
        return text
    return _extract_text_from_json_leak(text) or fallback


class AgentState:
    IDLE                        = "IDLE"
    WAITING_CONFIRMATION        = "WAITING_CONFIRMATION"        # User reviews plan
    EXECUTING                   = "EXECUTING"                   # Tools running
    WAITING_LOOP_CONTINUATION   = "WAITING_LOOP_CONTINUATION"   # Pagination cap hit — continue or stop?


class ChatAgent(BaseAgent):
    def __init__(self, connection_id: str):
        import time
        llm_mgr = get_llm_manager()
        super().__init__(llm_mgr)
        self.connection_id = connection_id
        self._state = AgentState.IDLE
        self.state_entered_at = time.time()
        self.is_processing = False
        self.plan: Optional[List[Dict]] = None

        # Fix 1: Session-wide monotonically increasing step counter.
        # Ensures step IDs are globally unique across turns (e.g. t1_step_1, t2_step_1)
        # so the Planner never confuses a stale step reference from chat history
        # with a live step in the current plan.
        self._turn_counter: int = 0

        # Fix 3: Structured recent tool results injected into the Planner context.
        # Keyed by tool_name -> truncated result string. Cleared each new turn.
        # This bypasses prose chat history entirely for the "act on what I just found" case.
        self._last_tool_results: List[Dict] = []  # [{tool, result_snippet}]

        # Reconnect resilience: cache the last plan response so it can be replayed
        # if the client's WebSocket dropped during LLM inference and reconnects.
        # Cleared when the plan is confirmed, cancelled, or a new plan is built.
        self._pending_response: Optional[str] = None

        # Fingerprint of the last plan actually proposed to the user —
        # compared against each freshly generated plan (see
        # _plan_signature/the retry loop in _handle_idle) to catch a
        # degraded small model anchoring on whatever tool call it just saw
        # succeed and echoing it verbatim for an unrelated new request,
        # instead of reasoning about the new message. Never cleared on
        # cancel — a cancelled plan being immediately repeated is just as
        # suspicious as an executed one being repeated.
        self._last_proposed_plan_signature: Optional[tuple] = None

        import threading
        self.cancel_event = threading.Event()

        # Bumped once per incoming user message (see websocket.py). A
        # cancelled turn's own blocked LLM call often can't actually be
        # interrupted mid-flight — llama.cpp's prefill/first-token step is a
        # single synchronous C call, so cancel_event has nothing to check
        # until it's already back at a Python-level loop iteration. Once
        # that orphaned call eventually returns, websocket.py compares its
        # captured generation id against this counter to tell a genuinely
        # superseded turn's result apart from the current one, and discards
        # it silently instead of popping in after the user has moved on.
        self.generation_id = 0

        # Instantiate sub-agents
        self.planner = PlannerAgent(llm_mgr)
        self.executor = ExecutorAgent(llm_mgr)
        self.planner.cancel_event = self.cancel_event
        self.executor.cancel_event = self.cancel_event

    async def _append_history(
        self, role: str, content: str, attachments: Optional[List[Dict]] = None, msg_type: Optional[str] = None
    ):
        """Asynchronously persist a chat message to SQLite via db_executor."""
        import asyncio
        loop = asyncio.get_running_loop()

        def _write():
            from app.db.database import SessionLocal
            from app.db.crud import add_chat_message
            db = SessionLocal()
            try:
                add_chat_message(db, self.connection_id, role, content, attachments=attachments, msg_type=msg_type)
            except Exception as e:
                logger.error(f"Critical failure saving chat message to DB: {e}")
                raise e
            finally:
                db.close()
                
        try:
            await loop.run_in_executor(db_executor, _write)
        except Exception as e:
            # Re-raise to abort the turn and allow handle_message to catch it
            raise RuntimeError(f"Database write failed: {e}")

    async def _get_history(self) -> List[Dict]:
        """Asynchronously load history from SQLite via db_executor."""
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _read():
            from app.db.database import SessionLocal
            from app.db.crud import get_chat_history
            db = SessionLocal()
            try:
                return get_chat_history(db, self.connection_id)
            except Exception as e:
                logger.error(f"Failed to load chat history from DB: {e}")
                return []
            finally:
                db.close()
                
        return await loop.run_in_executor(db_executor, _read)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        import time
        self._state = value
        self.state_entered_at = time.time()

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    # Verb token -> natural-language trigger hint. Real tool names in this
    # codebase mix two conventions — plain verb-first ("list_files",
    # "get_commits") and service-prefixed ("drive_list_files",
    # "github_search_repositories") — so matching is done against the set of
    # underscore-split tokens, not a name prefix, or every service-prefixed
    # tool would silently get no hint at all. A small local model benefits
    # far more from an explicit "use this when the request looks like X"
    # hint than from inferring intent purely from a terse, often
    # vendor-provided description.
    _USE_WHEN_HINTS = [
        ({"list"}, "the user wants to see/list existing items with no specific search term."),
        ({"search", "find"}, "the user gives a search term or keyword to look up."),
        ({"get", "read", "fetch"}, "the user refers to a specific, already-known item and wants its details or content."),
        ({"create", "send", "post", "write"}, "the user wants to create, send, or post something new."),
        ({"delete", "remove"}, "the user explicitly asks to delete or remove something."),
        ({"update", "edit"}, "the user wants to modify an existing item."),
    ]

    @classmethod
    def _derive_use_when(cls, name: str) -> str:
        lower = name.lower()
        if lower.startswith("web_"):
            return "the user gives a URL, or asks to look up/read/summarize a specific web page."
        tokens = set(lower.split("_"))
        for verbs, hint in cls._USE_WHEN_HINTS:
            if tokens & verbs:
                return hint
        return ""

    @classmethod
    def _format_tool_for_planner(cls, t: dict) -> str:
        """
        Serializes a single tool dict (MCP-sourced or local) into a rich,
        planner-readable block.

        Format:
            - tool_name: <description>
              REQUIRED args: arg1 (type) — description | arg2 (type) — description
              OPTIONAL args: arg3 (type) — description
              USE WHEN: <natural-language trigger hint derived from the tool name>
        """
        name = t.get("name", "")
        description = t.get("description", "").strip()
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))

        required_parts = []
        optional_parts = []
        for param, meta in properties.items():
            ptype = meta.get("type", "string")
            pdesc = meta.get("description", "").strip().rstrip(".")
            entry = f"{param} ({ptype})"
            if pdesc:
                entry += f" — {pdesc}"
            if param in required_fields:
                required_parts.append(entry)
            else:
                optional_parts.append(entry)

        lines = [f"- {name}: {description}"]
        if required_parts:
            lines.append(f"  REQUIRED args: {' | '.join(required_parts)}")
        if optional_parts:
            lines.append(f"  OPTIONAL args: {' | '.join(optional_parts)}")
        use_when = cls._derive_use_when(name)
        if use_when:
            lines.append(f"  USE WHEN: {use_when}")

        return "\n".join(lines)

    def _get_local_tools(self) -> List[Dict]:
        """
        Tools that aren't MCP servers: Playwright-based web tooling
        (app.core.scraper / app.core.browser_session — only offered when
        it's both installed via the Marketplace and not toggled off for
        this conversation via the '+' menu's Tools switch, both sharing the
        one 'playwright_scraper' capability since they're the same
        underlying Chromium install) plus sandboxed local-filesystem tools
        (app.core.filesystem_tools — always offered, no install step, since
        they're pure-Python stdlib and confined to the user's home
        directory by construction rather than needing a native download).

        Export-to-file used to live here too (export_document), but Agent
        Mode's planner proved unreliable at it on small local models — it
        would hallucinate a redundant fetch step (a fake URL for a document
        that was already sitting in context) before the real export step,
        breaking the whole plan. It's handled deterministically in Chat Mode
        instead now (see _handle_idle's mode == "chat" branch and
        _classify_export_intent/_execute_export_document) — no planner, no
        tool call, so nothing for the model to get wrong.
        """
        return self._get_scraper_tools() + self._get_filesystem_tool_defs()

    @staticmethod
    def _get_filesystem_tool_defs() -> List[Dict]:
        """
        Sandboxed local-filesystem tools — see app.core.filesystem_tools'
        module docstring for the exact sandbox model (confined to the
        user's home directory, denylisted sensitive paths/filenames, size
        caps). Read tools (search_local_files, read_file, list_folder) and
        the one write tool (write_file) are deliberately separate tools
        rather than one do-everything "filesystem" tool, so the
        plan-confirmation card shows the user exactly which kind of access
        each step is before they approve it. Named "search_local_files"
        rather than "search_files" specifically to avoid colliding with
        app.mcp.response_shapers' pre-existing Google-Drive-specific
        "search_files" entry (a different tool, a different result shape
        entirely — sharing the name would silently corrupt this tool's
        displayed results through that shaper).
        """
        return [
            {
                "name": "search_local_files",
                "description": (
                    "Searches the user's own laptop (confined to their home "
                    "directory — nothing outside it, and sensitive paths like "
                    ".ssh, Library, node_modules, and credential-shaped filenames "
                    "are automatically skipped) for files whose NAME contains a "
                    "given substring. Does not read file contents — use read_file "
                    "on a specific match afterward for that. Use when the user "
                    "asks to find, locate, or look for a file by name, extension, "
                    "date, or size, without already knowing its exact path."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Substring to match against filenames, case-insensitive."},
                        "root": {"type": "string", "description": "Optional subfolder to scope the search to (relative to the home directory, e.g. 'Documents'). Omit to search the whole home directory."},
                        "extension": {"type": "string", "description": "Optional file extension filter, without the dot (e.g. 'pdf')."},
                        "modified_after": {"type": "string", "description": "Optional ISO date (YYYY-MM-DD) — only files modified after this date."},
                        "modified_before": {"type": "string", "description": "Optional ISO date (YYYY-MM-DD) — only files modified before this date."},
                        "min_size_kb": {"type": "number", "description": "Optional minimum file size in KB."},
                        "max_size_kb": {"type": "number", "description": "Optional maximum file size in KB."},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "list_folder",
                "description": (
                    "Lists the immediate contents (files and subfolders, not "
                    "recursive) of a folder on the user's laptop, confined to "
                    "their home directory. Use to orient yourself in a directory "
                    "before searching or reading — e.g. the user says 'check my "
                    "Downloads folder' with no specific filename in mind."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Folder path, relative to the home directory (e.g. 'Downloads') or absolute. Omit to list the home directory itself."},
                    },
                },
            },
            {
                "name": "read_file",
                "description": (
                    "Reads and extracts the text content of a specific file on "
                    "the user's laptop by its exact path (get the path from "
                    "search_local_files or list_folder first if you don't already "
                    "have it) — confined to their home directory, credential-shaped "
                    "files refused. Supports PDF, DOCX, XLSX, PPTX, CSV, plain "
                    "text/markdown, and images (via OCR) — the same extraction "
                    "used for files the user uploads to chat, so a file already "
                    "on disk doesn't need to be manually uploaded first. Long "
                    "files come back in chunks — if the result reports "
                    "`has_more`, call this again with the same path and `offset` "
                    "set to the reported `next_offset` to keep reading."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Exact file path, relative to the home directory or absolute."},
                        "offset": {"type": "integer", "description": "Character offset to resume from — see `next_offset` in a prior result. Omit or 0 to start from the beginning."},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": (
                    "Writes plain-text content to a file on the user's laptop, "
                    "confined to their home directory. Fails if the file already "
                    "exists unless `overwrite` is explicitly set true — never "
                    "silently replaces an existing file. Use when the user asks "
                    "you to save something (a summary, a list, generated text) "
                    "to a real file on disk, as opposed to Chat Mode's "
                    "export-to-download-link for a PDF/DOCX/XLSX."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Destination file path, relative to the home directory or absolute. Parent folders are created if needed."},
                        "content": {"type": "string", "description": "The exact plain-text content to write."},
                        "overwrite": {"type": "boolean", "description": "Set true to replace an existing file at that path. Defaults to false (fails instead of clobbering)."},
                    },
                    "required": ["path", "content"],
                },
            },
        ]

    def _get_scraper_tools(self) -> List[Dict]:
        try:
            from app.core.scraper import is_chromium_installed
            if not is_chromium_installed():
                return []
            from app.db.crud import is_capability_active
            db = SessionLocal()
            try:
                if not is_capability_active(db, self.connection_id, "tool", "playwright_scraper"):
                    return []
            finally:
                db.close()
            return [{
                "name": "web_scrape",
                "description": (
                    "Opens a real headless browser to load a specific PUBLIC web page "
                    "(handles JavaScript-rendered sites a plain fetch can't) and returns "
                    "its text content for this conversation. Public sites only — every "
                    "visit is anonymous, with no login and no way to supply credentials or "
                    "cookies. If the result reports `needs_auth`, the page requires signing "
                    "in or is private; tell the user directly that you can't access it "
                    "rather than retrying. The content is NOT saved anywhere — if a later "
                    "question needs it again, call this tool again with the same URL. Use "
                    "when the user gives a URL, or asks to look up, read, fetch, or "
                    "summarize a specific web page it doesn't need to interact with — for a "
                    "page that needs clicking or a search box filled in first (but is still "
                    "public), use the browser_* tools instead, which share one real browser "
                    "session you can drive step by step. Long pages come back in chunks — "
                    "if the result reports `has_more`, call this again with the same url "
                    "and `offset` set to the reported `next_offset` to keep reading further "
                    "into the page, instead of getting the same beginning again."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The full URL to scrape, including https://"},
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Character offset into the page's extracted text to resume "
                                "from. Omit or pass 0 to start from the beginning. Use this "
                                "with the `next_offset` from a prior call on the same URL to "
                                "read further into a page that was too long for one chunk."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            }, *self._browser_tool_defs()]
        except Exception as e:
            logger.warning(f"Failed to resolve local tools: {e}")
            return []

    @staticmethod
    def _browser_tool_defs() -> List[Dict]:
        """
        Interactive Playwright tools sharing ONE persistent browser session
        per conversation (app.core.browser_session) — unlike web_scrape's
        fresh-launch-and-tear-down-per-call model, these let the agent
        navigate once and then click/fill/scroll/extract against that same
        live page across several separate tool calls: fill a search box,
        click "next", read the next page, etc. This is what actually makes
        "talk to a page like a developer would with Playwright" possible —
        web_scrape alone never could. Public sites only, same as web_scrape
        — every visit is anonymous, no login, no cookies.

        browser_screenshot is the one exception to "the agent drives this":
        it exists purely to show the user what the page currently looks
        like (see its own description) — the model itself has no vision
        capability in this app.
        """
        return [
            {
                "name": "browser_navigate",
                "description": (
                    "Opens a PUBLIC URL in the shared browser session for this "
                    "conversation, as a NEW tab that becomes the active one. Every other "
                    "browser_* tool call acts on whichever tab is currently active. Start "
                    "a browsing task with this before calling browser_click/fill/etc. "
                    "Anonymous only — if the result reports `needs_auth`, the page "
                    "requires signing in or is private; tell the user you can't access it "
                    "rather than retrying."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full URL, including https://"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "browser_click",
                "description": "Clicks an element on the currently active tab.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": (
                                "CSS selector of the element to click, e.g. "
                                "'button[type=submit]' or 'text=Log in'."
                            ),
                        },
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_fill",
                "description": (
                    "Types text into an input or textarea on the currently active tab, "
                    "replacing any existing value. Use for login forms, search boxes, etc."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector of the input, e.g. 'input[name=email]'."},
                        "text": {"type": "string", "description": "Text to type into the field."},
                    },
                    "required": ["selector", "text"],
                },
            },
            {
                "name": "browser_scroll",
                "description": (
                    "Scrolls the currently active tab. Use direction 'bottom' to trigger "
                    "infinite-scroll/lazy-loaded content before calling browser_extract_text."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["down", "up", "top", "bottom"],
                            "description": "Defaults to 'down' if omitted.",
                        },
                    },
                },
            },
            {
                "name": "browser_wait_for_selector",
                "description": (
                    "Waits for a CSS selector to appear on the currently active tab before "
                    "continuing. Use right after an action (click, navigate) that triggers "
                    "slow-loading content you need to then click, fill, or extract."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector to wait for."},
                        "timeout": {"type": "integer", "description": "Max milliseconds to wait. Defaults to 10000."},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_extract_text",
                "description": (
                    "Extracts the readable text content of the currently active tab, the "
                    "same way web_scrape does. Use after navigating/clicking to read what's "
                    "now on the page. Long pages come back in chunks — if the result "
                    "reports `has_more`, call this again with `offset` set to the reported "
                    "`next_offset` to keep reading."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Character offset to resume from — see `next_offset` in a "
                                "prior result. Omit or 0 to start from the beginning."
                            ),
                        },
                    },
                },
            },
            {
                "name": "browser_screenshot",
                "description": (
                    "Captures a screenshot of the currently active tab and shows it "
                    "directly to the USER in the chat — NOT to you, the model; you have "
                    "no vision capability in this app, so calling this tells you nothing "
                    "about what the page looks like. Use it only when the user explicitly "
                    "asks to see the page, or to show them the result of a flow you just "
                    "completed (login, checkout, a filled form) — never as a way to "
                    "inspect the page yourself; use browser_extract_text for that."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "full_page": {
                            "type": "boolean",
                            "description": "Capture the full scrollable page instead of just the visible viewport. Defaults to false.",
                        },
                    },
                },
            },
            {
                "name": "browser_go_back",
                "description": "Navigates the currently active tab back to its previous page in history.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "browser_go_forward",
                "description": "Navigates the currently active tab forward in history (after browser_go_back).",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "browser_list_tabs",
                "description": "Lists every open tab in the shared browser session, with its index, URL, and title.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "browser_switch_tab",
                "description": "Makes an already-open tab (by its index from browser_list_tabs) the active one for subsequent browser_* calls.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"index": {"type": "integer", "description": "Tab index from browser_list_tabs."}},
                    "required": ["index"],
                },
            },
            {
                "name": "browser_close",
                "description": (
                    "Closes the shared browser session for this conversation, ending the "
                    "browsing task. Not required after every use — only call this when "
                    "genuinely done, since the next browser_* call would otherwise reuse "
                    "the same open tabs."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def _all_available_tools(self) -> List[Dict]:
        """
        Single source of truth for "every tool that exists" — currently just
        local tools (e.g. web_scrape). EVERY consumer that needs the full
        tool list (plan validation, execution, prompt building, the
        query-rewrite pass) MUST call this rather than reconstructing the
        list by hand. Used to also merge in MCP-connected servers' tools
        (mcp_registry.list_all_tools()) — removed along with the rest of
        this class's MCP integration; app/mcp/ itself is untouched, this
        just no longer calls into it.
        """
        return self._get_local_tools()


    # The only bound on how much of a scraped page reaches the LLM per call
    # — response_shapers.py's web_scrape shaper forwards text_preview
    # untouched (it used to silently re-truncate to 2000 regardless of this
    # value, which also dropped the continuation note entirely; both are
    # fixed now, see _web_scrape_exec's docstring). ~6000 chars is a rough
    # budget-conscious default for a single tool result among possibly
    # several in one turn; the offset/next_offset/has_more mechanism below
    # is how the model reads further into a longer page instead of forcing
    # every chunk to fit at once.
    _SCRAPE_CHUNK_CHARS = 6000

    def _chunk_text(self, full_text: str, offset: int) -> Dict:
        """
        Shared by _execute_web_scrape and _execute_browser_action's
        extract_text — slices `full_text` into one _SCRAPE_CHUNK_CHARS
        window starting at `offset`, reporting whether there's more so the
        caller can walk further into a page longer than one chunk instead
        of only ever seeing the first chunk again on a re-call.
        """
        offset = max(0, offset)
        chunk = full_text[offset:offset + self._SCRAPE_CHUNK_CHARS]
        has_more = offset + len(chunk) < len(full_text)
        return {
            "text": chunk,
            "total_length": len(full_text),
            "offset": offset,
            "next_offset": (offset + len(chunk)) if has_more else None,
            "has_more": has_more,
        }

    @staticmethod
    def _parse_offset(arguments: Dict) -> int:
        try:
            return max(0, int(arguments.get("offset") or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _continuation_note(tool_name: str, outcome: Dict) -> str:
        """
        Builds the note appended to a successful web_scrape / browser_navigate
        / browser_extract_text / read_file result, telling the model how to
        read further into content that didn't fit in one chunk, and/or (web
        tools only) that the page needs login and can't be accessed (public
        sites only — see app.core.scraper's module docstring). Only these
        four tool names ever set has_more/needs_auth on their outcome —
        every other browser_*/filesystem tool never calls this.
        """
        parts = []
        if outcome.get("has_more"):
            if tool_name == "web_scrape":
                call_hint = f"Call web_scrape again with the same url and offset={outcome['next_offset']}"
            elif tool_name == "read_file":
                call_hint = f"Call read_file again with the same path and offset={outcome['next_offset']}"
            else:
                call_hint = f"Call browser_extract_text with offset={outcome['next_offset']}"
            parts.append(
                f"Showing characters {outcome['offset']}-{outcome['offset'] + len(outcome['text'])} "
                f"of {outcome['total_length']}. {call_hint} to keep reading."
            )
        if outcome.get("needs_auth"):
            parts.append(
                "This page requires signing in or is private — this tool only accesses "
                "public pages, so tell the user directly that you can't access it rather "
                "than treating the text above (if any) as the full content."
            )
        return " ".join(parts)

    async def _execute_web_scrape(self, arguments: Dict) -> Dict:
        """
        Dispatch for the web_scrape local tool — used by Agent Mode's
        execution loop only; Chat Mode does no tool calling at all (see
        _handle_idle's mode == "chat" branch, which nudges to Agent Mode on
        a detected URL instead). Deliberately ephemeral: a scrape is shown in
        the conversation (injected into the assistant's step result) and NOT
        persisted anywhere beyond ordinary chat history — no UserDocument
        row, no Qdrant embedding, no Files panel entry. Only files the user
        explicitly uploads go into the vector DB; see app.core.scraper.scrape_url,
        which this calls directly with no ingestion pipeline involved.

        Public pages only — every visit is anonymous (see
        app.core.scraper's module docstring for why there's no cookie/login
        retry path); a page that needs auth just comes back with
        needs_auth=True for the caller to tell the user about.

        `arguments["offset"]` (see the tool's inputSchema and _chunk_text)
        slices which part of the freshly extracted text comes back — the
        page itself is always re-fetched live (there's no cache), but the
        offset lets a follow-up call walk further into the same page's text
        instead of only ever seeing the first chunk again, which used to be
        a dead end for any page longer than one chunk.

        Returns {success, title, text, total_length, offset, next_offset,
        has_more, warnings, needs_auth, error}.
        """
        from app.core.scraper import scrape_url

        url = arguments.get("url", "")
        offset = self._parse_offset(arguments)
        result = await scrape_url(url)

        return {
            "success": result.success,
            "title": result.title,
            **self._chunk_text(result.text, offset),
            "warnings": result.warnings,
            "needs_auth": result.needs_auth,
            "error": result.error,
        }

    _FILESYSTEM_TOOL_NAMES = {"search_local_files", "list_folder", "read_file", "write_file"}
    # Every filesystem tool except write_file only ever looks at the disk —
    # used for the plan-confirmation card's [read-only]/[writes] badge.
    _READ_ONLY_TOOL_NAMES = {"web_scrape", "search_local_files", "list_folder", "read_file"}

    async def _execute_filesystem_tool(self, tool_name: str, arguments: Dict) -> Dict:
        """
        Dispatch for the sandboxed local-filesystem tools (see
        _get_filesystem_tool_defs and app.core.filesystem_tools' module
        docstring for the sandbox model). Each underlying function is
        synchronous, blocking I/O (os.walk, file reads, and — for read_file
        — potentially OCR/transcription via extract_text), so it's run off
        the event loop the same way _execute_browser_action's session calls
        are. Every SandboxError (path outside the sandbox, denied dir/file)
        is caught here and turned into a normal {"success": False, "error":
        ...} outcome instead of propagating — the executor's generic
        exception handler further up would otherwise report it as an
        "Internal bug" rather than the plain refusal it actually is.
        """
        from app.core.filesystem_tools import (
            search_files, list_folder, read_file_text, write_file, SandboxError,
        )

        try:
            if tool_name == "search_local_files":
                return await anyio.to_thread.run_sync(lambda: search_files(
                    query=arguments.get("query", ""),
                    root=arguments.get("root"),
                    extension=arguments.get("extension"),
                    modified_after=arguments.get("modified_after"),
                    modified_before=arguments.get("modified_before"),
                    min_size_kb=arguments.get("min_size_kb"),
                    max_size_kb=arguments.get("max_size_kb"),
                ))

            if tool_name == "list_folder":
                return await anyio.to_thread.run_sync(lambda: list_folder(arguments.get("path")))

            if tool_name == "read_file":
                offset = self._parse_offset(arguments)
                outcome = await anyio.to_thread.run_sync(lambda: read_file_text(arguments.get("path", "")))
                if not outcome["success"]:
                    return outcome
                chunked = self._chunk_text(outcome["text"], offset)
                return {
                    "success": True,
                    "path": outcome["path"],
                    "file_type": outcome["file_type"],
                    **chunked,
                }

            if tool_name == "write_file":
                return await anyio.to_thread.run_sync(lambda: write_file(
                    path=arguments.get("path", ""),
                    content=arguments.get("content", ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                ))

            return {"success": False, "error": f"Unknown filesystem tool '{tool_name}'."}
        except SandboxError as e:
            return {"success": False, "error": str(e)}

    async def _execute_browser_action(self, tool_name: str, arguments: Dict) -> Dict:
        """
        Dispatch for the browser_* local tools (see _browser_tool_defs) —
        all of them act on the ONE persistent Playwright session shared for
        this conversation (app.core.browser_session), unlike web_scrape's
        fresh-launch-and-tear-down-per-call model. That's what lets the
        agent navigate once and then click/fill/scroll/extract against the
        same live page across several separate tool calls.

        `arguments` is passed straight through to the Node driver
        (app/core/browser_driver.js) as the command's `params` — the tool
        schemas were written to match the driver's own parameter names
        (selector, text, url, direction, index, full_page), so no
        translation layer is needed between them, except for extract_text's
        text-vs-html handling and offset chunking, done here.

        Returns {success, ...action-specific fields} or {success: False,
        error}. Never raises — a failed action (bad selector, closed tab,
        crashed driver process) is a normal, recoverable tool result, not
        an exception that should abort the whole plan.
        """
        from app.core.browser_session import get_session, close_session

        action = tool_name[len("browser_"):]
        session = get_session(self.connection_id)

        try:
            if action == "close":
                await anyio.to_thread.run_sync(session.close)
                close_session(self.connection_id)
                return {"success": True, "closed": True}

            raw = await anyio.to_thread.run_sync(lambda: session.send(action, dict(arguments)))

            if action == "navigate":
                # Reuses scraper.py's exact bot-check/login-wall detection —
                # the driver's navigate response is the same shape
                # (title/html/status/timedOut/hasPasswordField)
                # scraper_driver.js's one-shot script returns, so
                # browser_navigate gets identical needs_auth handling to
                # web_scrape instead of silently having none. Public pages
                # only — see app.core.scraper's module docstring.
                from app.core.scraper import build_scrape_result
                url = arguments.get("url", "")
                scraped = build_scrape_result(url, raw)
                return {
                    "success": scraped.success,
                    "title": scraped.title,
                    "url": raw.get("url", url),
                    "tab_index": raw.get("tabIndex"),
                    **self._chunk_text(scraped.text, 0),
                    "warnings": scraped.warnings,
                    "needs_auth": scraped.needs_auth,
                    "error": scraped.error,
                }

            if action == "extract_text":
                import trafilatura
                html = raw.pop("html", "") or ""
                text = trafilatura.extract(html, favor_recall=True) or ""
                offset = self._parse_offset(arguments)
                raw.update(self._chunk_text(text, offset))

            return {"success": True, **raw}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _execute_export_document(self, arguments: Dict) -> Dict:
        """
        Converts markdown to a file (app.core.exporter.export_markdown) and
        stores it under a short-lived ID (app.api.export.store_export)
        instead of returning the bytes themselves — the caller (Chat Mode's
        deterministic export-intent check in _handle_idle; see that
        method's mode == "chat" branch) isn't an HTTP handler, so there's no
        request/response cycle to hand raw file bytes back through. The
        returned download_url is what actually lets the user get the file:
        an absolute link to this backend's own /api/export/download/{id}
        route, rendered as a normal markdown link in the chat message.

        Not offered as an Agent Mode tool anymore (see _get_local_tools'
        docstring for why) — called directly as a plain function instead of
        through the planner/tool-call machinery.
        """
        from app.core.exporter import export_markdown, CONTENT_TYPES
        from app.api.export import store_export, BACKEND_BASE_URL, _safe_filename

        content = arguments.get("content", "")
        fmt = (arguments.get("format") or "").lower().strip()
        title = arguments.get("title", "")

        if fmt not in CONTENT_TYPES:
            return {"success": False, "error": f"Unsupported format '{fmt}'. Use pdf, docx, or xlsx."}
        if not content.strip():
            return {"success": False, "error": "Nothing to export — content was empty."}

        try:
            data = await anyio.to_thread.run_sync(export_markdown, content, fmt, title)
        except Exception as e:
            return {"success": False, "error": f"Export failed: {e}"}

        filename = _safe_filename(title, fmt)
        export_id = store_export(data, CONTENT_TYPES[fmt], filename)
        return {
            "success": True,
            "filename": filename,
            "download_url": f"{BACKEND_BASE_URL}/api/export/download/{export_id}",
            "size_bytes": len(data),
        }

    _URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

    @classmethod
    def _extract_url(cls, message: str) -> Optional[str]:
        m = cls._URL_RE.search(message)
        return m.group(0).rstrip('.,;:!?') if m else None

    # Two deliberately-narrow shapes, to keep false positives (ordinary
    # prose with a stray slash — "and/or", "9/5", "his/her") out: either an
    # unambiguous path prefix (/, ~/, ./, ../) at a word boundary followed
    # by non-space chars — the (?<!\w) lookbehind is what excludes a
    # mid-word slash like the "/or" in "and/or" — or any non-space run
    # containing a "/" whose final segment ends in a recognizable file
    # extension (e.g. "aegis_agent_test_dir/review.txt").
    _PATH_RE = re.compile(
        r'(?<!\w)(?:~|\.{1,2})?/[^\s"\')\]]+'
        r'|[^\s"\')\]]+/[^\s"\')\]]*\.[A-Za-z0-9]{1,8}\b'
    )

    @classmethod
    def _extract_path_like(cls, message: str) -> Optional[str]:
        m = cls._PATH_RE.search(message)
        return m.group(0).rstrip('.,;:!?') if m else None

    @staticmethod
    def _plan_signature(plan_steps: List[Dict]) -> tuple:
        """Order-independent fingerprint of a plan's (tool, arguments) pairs
        — used to detect a freshly generated plan that's identical to the
        last one actually proposed, regardless of step_id/depends_on, which
        legitimately differ turn to turn."""
        return tuple(sorted(
            (step.get("tool"), json.dumps(step.get("arguments", {}), sort_keys=True))
            for step in plan_steps
        ))

    # Deliberately loose — this only gates whether the LLM classification
    # call below runs at all, not whether export actually fires. A single
    # hint word is enough to spend one small LLM call finding out; the
    # overwhelming majority of messages (greetings, questions, coding asks)
    # match none of these and skip the call entirely, at zero cost.
    _EXPORT_HINT_RE = re.compile(
        r'\b(export|download|save|convert|pdf|docx?|xlsx|excel|word|spreadsheet|file)\b',
        re.IGNORECASE,
    )

    _FAKE_TOOL_NARRATION_RE = re.compile(
        r'\n\s*\**\s*Step\s*1\s*[:.]|\bweb_scrape\b|\bexport_document\b|\n```json'
        r'|\[Download [^\]]*\]\(https?://[^)]*\)'
        # Self-denial commentary: despite being told the export happens
        # automatically, the model sometimes still claims it *can't* and
        # suggests an external tool instead — none of that belongs in the
        # actual exported file, just the real answer/content above it.
        r"|\bI (?:don't|do not|can't|cannot|'m not able to) (?:have the capability to |directly )?"
        r'(?:create|generate|export|produce|make) (?:a |an )?(?:PDF|DOCX|XLSX|Word|Excel)\b'
        r'|\bAdobe Acrobat\b|\byou would need to use\b|\bPDF creation tool\b',
        re.IGNORECASE,
    )

    @classmethod
    def _clean_export_content(cls, text: str) -> str:
        """
        Truncates at the first sign of hallucinated tool-call narration or
        self-denial commentary ("I can't create a PDF, try Adobe Acrobat").
        Chat Mode has no real tools and is explicitly told the export
        happens automatically, but a small/degraded local model sometimes
        ignores that anyway — exporting its confused commentary verbatim
        would bake it into the downloaded file instead of just the actual
        answer/content the user asked for.

        Trims back to the last paragraph break before the match rather than
        the exact match offset — the match itself typically lands mid-line
        (e.g. on the tool name inside "- Step 1: `web_scrape`"), and cutting
        there would leave a dangling markdown fragment in the export instead
        of cleanly dropping the whole hallucinated section.
        """
        m = cls._FAKE_TOOL_NARRATION_RE.search(text)
        if not m:
            return text
        cutoff = text.rfind("\n\n", 0, m.start())
        if cutoff == -1:
            cutoff = text.rfind("\n", 0, m.start())
        if cutoff == -1:
            cutoff = 0
        return text[:cutoff].rstrip()

    def _extract_export_content_via_llm(self, raw_response: str, export_fmt: str) -> str:
        """
        Fallback for when the model didn't use the ```export fence at all —
        a second, small LLM call whose only job is extracting the clean
        final content that should go into the exported file, instead of
        denylisting our way through every possible way it could have
        phrased narration/self-doubt/filler around the real answer.
        _clean_export_content is a fixed set of known-bad patterns; this
        can recognize and strip ANY kind of surrounding noise, including
        phrasing never seen before (e.g. observed once: a clean tagline
        followed by "Export the exact tagline as a PDF: <tagline again>" —
        not false, just redundant filler the denylist has no pattern for).

        Only spent when the fast path (the model actually used the fence)
        fails, so the common compliant case costs nothing extra.
        """
        llm = self.get_llm()
        if not llm:
            return self._clean_export_content(raw_response)

        prompt = f"""Below is an assistant's response to a user. Extract ONLY the final content that should be saved into a {export_fmt.upper()} file — the actual answer, summary, or data, nothing else.

Response:
\"\"\"
{raw_response}
\"\"\"

Rules:
- Strip any meta-commentary, mentions of tools/modes/capabilities, apologies, or claims about what the assistant can or can't do.
- Strip any narration about steps, plans, or exporting itself, and any redundant restatement of the content that follows it.
- Keep the actual substantive content exactly as written, including markdown formatting (headings, lists, tables).
- If truly nothing usable remains, output nothing.

Output ONLY the extracted content — no preamble, no surrounding quotes, no explanation of what you did."""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1024,
            )
            extracted = response["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Export content extraction failed: {e}")
            return self._clean_export_content(raw_response)

        # Belt-and-suspenders: still run the denylist pass in case the
        # extractor itself left some narration in, or returned nothing.
        return self._clean_export_content(extracted) if extracted else self._clean_export_content(raw_response)

    def _classify_export_intent(self, message: str) -> Optional[str]:
        """
        Light LLM classification pass for "does this message ask to export/
        download/save/convert the answer into a file, and which format" —
        same cheap-regex-gate-then-LLM pattern used throughout this class.
        Replaces an earlier rigid regex (required an exact action word like
        "export" AND an exact format word like "pdf" in the same message) that missed
        anything phrased differently — "can I get this as a file I can
        keep", "turn that into something I can send someone" — since intent
        is what actually matters here, not which synonyms were used.

        Runs off the event loop via llm_executor by callers, same as every
        other LLM call in this class — this is a sync method.
        """
        if not self._EXPORT_HINT_RE.search(message):
            return None

        llm = self.get_llm()
        if not llm:
            return None

        prompt = f"""Does this message ask to export, download, save, or convert the assistant's answer into a downloadable file?

Message: "{message}"

Output a JSON object with two keys:
- "is_export": true only if the user wants a FILE created from this conversation's content — not just a question that happens to mention a file/document, and not a request to read or open something that already exists.
- "format": one of "pdf", "docx", "xlsx" if is_export is true (closest match — e.g. "word document" -> "docx", "spreadsheet"/"excel" -> "xlsx", anything else -> "pdf"), otherwise null.

Output valid JSON only. Example: {{"is_export": true, "format": "docx"}}"""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=30,
                response_format={"type": "json_object"},
            )
            content = response["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            fmt = data.get("format")
            if data.get("is_export") and fmt in ("pdf", "docx", "xlsx"):
                return fmt
            return None
        except Exception as e:
            logger.warning(f"Export-intent classification failed: {e}")
            return None

    # Same cost-control idea as _EXPORT_HINT_RE: cheap gate on whether it's
    # even worth the classification call below. Requires BOTH a question
    # mark and a connector word — a single "?" alone (the overwhelming
    # majority of messages) skips the call entirely.
    _COMPOUND_QUESTION_MARK_RE = re.compile(r'\?')
    _COMPOUND_CONNECTOR_RE = re.compile(r'\b(and|also|as well as|additionally)\b', re.IGNORECASE)

    def _decompose_compound_question(self, message: str) -> Optional[List[str]]:
        """
        Light LLM pass: does this message actually contain multiple
        distinct questions/requests bundled into one, e.g. "which items
        are out of stock, and what is the unit price of the Mechanical
        Keyboard?" — reproduced dropping the first half and only answering
        the second. Returns the parts as a list (2+) if so, else None.

        Chat Mode only (see its call site) — the failure mode observed was
        a document-Q&A answer silently addressing only the last clause of a
        two-part question; injecting the parts explicitly into the prompt
        gives the model an itemized checklist instead of one run-on ask it
        can partially skim.
        """
        if not (self._COMPOUND_QUESTION_MARK_RE.search(message) and self._COMPOUND_CONNECTOR_RE.search(message)):
            return None

        llm = self.get_llm()
        if not llm:
            return None

        prompt = f"""Does this message contain MULTIPLE distinct questions or requests that each need their own separate answer — not just one question with extra detail or a single compound noun phrase?

Message: "{message}"

Output a JSON object with one key:
- "parts": a list of the distinct questions/requests as short strings, in order, ONLY if there are 2 or more genuinely separate asks. Otherwise an empty list.

Output valid JSON only. Example: {{"parts": ["which items are out of stock", "what is the unit price of the Mechanical Keyboard"]}}"""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            content = response["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            parts = data.get("parts")
            if isinstance(parts, list) and len(parts) >= 2:
                return [str(p) for p in parts if str(p).strip()]
            return None
        except Exception as e:
            logger.warning(f"Compound-question decomposition failed: {e}")
            return None

    def _classify_export_and_compound(self, message: str) -> tuple[Optional[str], Optional[List[str]]]:
        """
        Combined variant of _classify_export_intent + _decompose_compound_
        question — one LLM prefill answering both questions instead of two
        serialized ones. Only used at the call site when BOTH cheap gates
        (_EXPORT_HINT_RE and the compound-question pair) fire on the same
        message; when only one fires, calling that single-purpose method
        directly stays cheaper AND more reliable for a small model than
        asking a combined prompt it didn't need to answer.
        """
        llm = self.get_llm()
        if not llm:
            return None, None

        prompt = f"""Analyze this message and answer two independent questions about it.

Message: "{message}"

Output a JSON object with three keys:
- "is_export": true only if the user wants a FILE created from this conversation's content — not just a question that happens to mention a file/document, and not a request to read or open something that already exists.
- "format": one of "pdf", "docx", "xlsx" if is_export is true (closest match — e.g. "word document" -> "docx", "spreadsheet"/"excel" -> "xlsx", anything else -> "pdf"), otherwise null.
- "parts": a list of the distinct questions/requests as short strings, in order, ONLY if the message bundles 2 or more genuinely separate asks that each need their own answer. Otherwise an empty list.

Output valid JSON only. Example: {{"is_export": true, "format": "docx", "parts": []}}"""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            content = response["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            fmt = data.get("format")
            export_fmt = fmt if data.get("is_export") and fmt in ("pdf", "docx", "xlsx") else None
            parts = data.get("parts")
            compound_parts = (
                [str(p) for p in parts if str(p).strip()]
                if isinstance(parts, list) and len(parts) >= 2 else None
            )
            return export_fmt, compound_parts
        except Exception as e:
            logger.warning(f"Combined export/compound classification failed: {e}")
            return None, None

    @staticmethod
    def _matches_any_keyword(message: str, keywords) -> bool:
        """
        Word-boundary keyword match for yes/no/cancel-style intent detection.
        Plain substring matching (`"kw" in message`) false-positives constantly
        in practice — "yes" inside "yesterday", "no" inside a random cookie
        value like "sess_no8x92jf...", "stop" inside "don't stop halfway",
        "ok" inside "cookie". Requiring a real word boundary on each side
        fixes all of these without losing multi-word phrase matches like
        "go ahead" (the boundary sits at the very start/end of the phrase).
        """
        low = message.strip().lower()
        return any(re.search(rf"\b{re.escape(kw)}\b", low) for kw in keywords)

    def get_available_tools(self) -> str:
        """Fetches all available local tools (e.g. web_scrape)."""
        tools = self._all_available_tools()
        if not tools:
            return "No local tools are currently active."
        return "\n".join(self._format_tool_for_planner(t) for t in tools)

    # Cheap gate replacing an LLM call: back when the registry mixed local
    # tools with many MCP-connected ones, an LLM pass ranked the 1-5 most
    # relevant before handing them to the planner. With MCP tools removed
    # (see _all_available_tools' docstring), the registry is just a
    # handful of local tools — nothing left to rank — so get_searched_tools
    # below hands the planner all of them directly. is_counting still
    # needs detecting (it flips list/search steps to fetch_scope
    # "exhaustive"), just via regex instead of a now-pointless LLM call.
    _COUNTING_HINT_RE = re.compile(
        r'\b(how many|how much|count of|total number|number of|list all|all of the|every file|everything in|complete list)\b',
        re.IGNORECASE,
    )

    def get_searched_tools(self, query: str) -> tuple[str, bool, List[str]]:
        """
        Returns every local tool directly, plus a cheap regex-based
        is_counting flag — see _COUNTING_HINT_RE's comment for why this no
        longer needs an LLM call. Kept as its own method (rather than
        inlining at call sites) since callers still expect this three-item
        shape, and _all_available_tools() is the single source of truth
        for "every tool that exists".
        """
        all_tools = self._all_available_tools()
        if not all_tools:
            return "", False, []
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in all_tools)
        is_counting = bool(self._COUNTING_HINT_RE.search(query))
        return tools_str, is_counting, [t["name"] for t in all_tools]

    def _get_entity_context(self) -> str:
        """Loads confirmed session entities from SQLite and returns the context block."""
        try:
            db = SessionLocal()
            block = build_entity_context_block(db, self.connection_id)
            db.close()
            return block
        except Exception as e:
            logger.warning(f"Could not load entity context: {e}")
            return ""

    def _call_llm_json(self, messages):
        llm = self.get_llm()
        if not llm:
            return "{}"
        try:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                stream=True
            )
            full_response = ""
            for chunk in response:
                if getattr(self, "cancel_event", None) and self.cancel_event.is_set():
                    logger.info("JSON generation cancelled.")
                    break
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        full_response += delta["content"]
            self._log_token_usage(llm, messages, full_response, "chat", self.connection_id)
            return full_response
        except Exception as e:
            logger.error(f"LLM JSON call failed: {e}")
            return "{}"

    def _call_llm_text(self, messages, token_callback=None):
        llm = self.get_llm()
        if not llm:
            return ""

        def _generate(msgs, forward_live: bool):
            """
            Runs one streamed completion, always buffering the full text.
            When forward_live is True, also pushes tokens to token_callback
            as they arrive — UNTIL the very first non-whitespace character
            reveals the response has degenerated into a bare JSON object/
            array, at which point it stops forwarding and abandons the
            generation early (breaking this loop halts further decode
            steps, since llama-cpp-python's streaming is pull-based) rather
            than let more of a leak reach the user or waste compute on a
            response about to be discarded anyway. The normal case — the
            overwhelming majority of turns — is a pure pass-through with
            zero added latency; a leak is caught before more than a
            character or two could ever be shown.
            """
            response = llm.create_chat_completion(messages=msgs, temperature=0.7, stream=True)
            buf = ""
            checked_start = False
            leaking = False
            for chunk in response:
                if getattr(self, "cancel_event", None) and self.cancel_event.is_set():
                    logger.info("Text generation cancelled.")
                    break
                if "choices" not in chunk or not chunk["choices"]:
                    continue
                token = chunk["choices"][0].get("delta", {}).get("content")
                if not token:
                    continue
                buf += token
                if not forward_live:
                    continue
                if not checked_start and buf.strip():
                    checked_start = True
                    if buf.lstrip()[:1] in ("{", "["):
                        leaking = True
                if leaking:
                    break
                if token_callback:
                    token_callback(token)
            return buf, leaking

        try:
            full_response, leaking = _generate(messages, forward_live=True)

            # Verified live against this app's own bundled model: a small
            # local model sharing history with Agent Mode's plan/tool-
            # result JSON blocks (see build_chat_prompt's rule 3) sometimes
            # imitates that shape and replies with a bare JSON object
            # instead of prose, despite the prompt's explicit "NEVER output
            # JSON" instruction. _looks_like_pure_json is a strict
            # json.loads check on the finished text, not just "starts with
            # a brace" — prose that happens to mention one isn't a false
            # positive. One corrective regeneration, non-streamed so a
            # second leak is never shown mid-stream either, then a
            # best-effort text extraction — never the raw JSON itself —
            # as the last resort.
            if leaking or _looks_like_pure_json(full_response):
                logger.warning("Chat answer degenerated into raw JSON — regenerating with a corrective instruction.")
                reinforced = messages + [{
                    "role": "system",
                    "content": (
                        "Your previous reply was a raw JSON object instead of a real answer. "
                        "Respond in plain natural-language sentences only — no JSON, no braces, "
                        "no code fence wrapping the whole reply."
                    ),
                }]
                full_response, _ = _generate(reinforced, forward_live=False)

                if _looks_like_pure_json(full_response):
                    full_response = (
                        _extract_text_from_json_leak(full_response)
                        or "Sorry, I had trouble putting that into words — could you rephrase your question?"
                    )

                if token_callback:
                    token_callback(full_response)

            self._log_token_usage(llm, messages, full_response, "chat", self.connection_id)
            return full_response
        except Exception as e:
            logger.error(f"LLM TEXT call failed: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # State machine
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_message(self, message: str, mode: str = "chat", token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None, export_format: Optional[str] = None) -> str:
        """Main state machine dispatcher."""

        if message == "__system_mode_switch__":
            if self.state == AgentState.WAITING_CONFIRMATION:
                self.state = AgentState.IDLE
                self.plan = None
                return "__system_toast__:Pending action discarded."
            return ""

        if self.state == AgentState.IDLE:
            return await self._handle_idle(message, mode, token_callback, status_callback, attachments, export_format)

        elif self.state == AgentState.WAITING_CONFIRMATION:
            return await self._handle_confirmation(message, token_callback)

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."

        return "Unknown state."

    async def _get_document_context(self, message: str, attachments: Optional[List[Dict]], status_callback=None) -> str:
        """
        Waits for any documents attached to *this* message to finish
        ingesting, then runs RAG retrieval scoped to this conversation and
        returns a "Relevant excerpts from your uploaded documents" block
        (empty string if none). Shared by Chat Mode's answer generation and
        Agent Mode's plan generation — Agent Mode needs this context too,
        otherwise it has no way to know an uploaded document even exists and
        will hallucinate a URL/tool for "the report I uploaded earlier"
        instead of just reading it.
        """
        attached_ids = [a["document_id"] for a in (attachments or []) if a.get("document_id") is not None]
        if attached_ids:
            if status_callback:
                await status_callback("Processing your document...")

            import asyncio as _asyncio

            def _all_ready(ids: List[int]) -> bool:
                from app.db.models import UserDocument
                _db = SessionLocal()
                try:
                    rows = _db.query(UserDocument).filter(UserDocument.id.in_(ids)).all()
                    return all(r.status in ("ready", "failed") for r in rows) and len(rows) == len(ids)
                finally:
                    _db.close()

            loop = _asyncio.get_running_loop()
            still_processing = True
            for _ in range(20):  # ~20s ceiling, then proceed best-effort
                if await loop.run_in_executor(db_executor, _all_ready, attached_ids):
                    still_processing = False
                    break
                await _asyncio.sleep(1)

            # Past the ceiling with ingestion still not done — this is the
            # ONLY turn that knows that; every other RAG-search codepath
            # below has no way to tell "no context because there's nothing
            # to find" apart from "no context because it isn't ready yet",
            # and left alone will produce exactly the confusing "I can't
            # access files" reply instead of "give it a moment". A first
            # document upload can genuinely take a while the very first
            # time (embedding/reranker models finish loading in the
            # background — see main.py's startup preload and the
            # _model_init_lock note in rag/processor.py), so say so
            # explicitly rather than silently pretending nothing is attached.
            if still_processing:
                return (
                    "[SYSTEM NOTE]: The document(s) attached to this message are still being "
                    "processed (this can take longer than usual the first time, while local "
                    "search models finish loading) — their content is not available yet. Tell "
                    "the user their document is still processing and to try their question "
                    "again in a moment. Do NOT claim you have no way to access uploaded files."
                )

        # An image whose OCR was skipped at upload time (see documents.py's
        # skip_ocr) has NO searchable content in Qdrant at all — it was only
        # ever going to be readable via _attach_vision_images sending it as
        # real image input. If the active model has since changed (or its
        # mmproj is no longer paired) by the time this turn actually sends,
        # that image now has no representation whatsoever — surface that
        # explicitly rather than silently answering as if nothing were
        # attached, which is indistinguishable from the model just not
        # noticing the attachment.
        vision_gap_note = ""
        if attached_ids and not self.get_active_vision_mmproj_path():
            from app.db.models import UserDocument
            _db = SessionLocal()
            try:
                stranded = _db.query(UserDocument).filter(
                    UserDocument.id.in_(attached_ids),
                    UserDocument.ocr_skipped_for_vision == True,
                ).all()
            finally:
                _db.close()
            if stranded:
                names = ", ".join(d.filename for d in stranded)
                vision_gap_note = (
                    f"[SYSTEM NOTE]: {names} was uploaded while a vision-capable model was "
                    "active, so its content was never OCR'd or indexed — it was only ever "
                    "readable by a vision model looking at it directly. The currently active "
                    "model is no longer vision-capable, so this image's content is NOT "
                    "available right now. Tell the user to switch back to a vision-capable "
                    "model (LLM panel) and re-send to have it read, rather than answering as "
                    "if the image isn't there.\n\n"
                )

        if status_callback:
            await status_callback("Searching your documents...")

        from app.core import context_config as ctx_cfg
        max_rag_chunks = ctx_cfg.get("chat").get("max_rag_chunks", 5)

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            from app.core.rag.processor import hybrid_search
            relevant_chunks = await loop.run_in_executor(
                db_executor,
                lambda: hybrid_search(query=message, conversation_id=self.connection_id, top_k=max_rag_chunks)
            )
        except Exception as e:
            logger.warning(f"RAG search failed: {e}")
            relevant_chunks = []

        if not relevant_chunks:
            logger.info(f"RAG retrieved 0 chunks for query: {message}")
            return vision_gap_note

        logger.info(f"RAG retrieved {len(relevant_chunks)} chunks for query: {message}")
        document_context = vision_gap_note + "Relevant excerpts from your uploaded documents:\n\n"
        for chunk in relevant_chunks:
            document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"
        return document_context

    async def _handle_idle(self, message: str, mode: str = "chat", token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None, export_format: Optional[str] = None) -> str:
        """IDLE → generate plan → WAITING_CONFIRMATION."""
        await self._append_history("user", message, attachments=attachments)
        
        # ── Early check: ensure a model is actually downloaded ──────────────
        test_llm = self.get_llm()
        if test_llm is None:
            no_model_msg = (
                "**No AI model is loaded.** Please visit the **LLM Panel** in the sidebar "
                "to download a model (e.g., Qwen 2.5 3B). Once downloaded, come back and try again.\n\n"
                "The download only needs to happen once — after that, the model stays resident in memory."
            )
            await self._append_history("assistant", no_model_msg)
            return no_model_msg
        
        entity_context = self._get_entity_context()


        # Load context window config live from the JSON config store so changes
        # from the Context Management UI take effect without a backend restart.
        from app.core import context_config as ctx_cfg
        _planner_cfg = ctx_cfg.get("planner")
        _chat_cfg    = ctx_cfg.get("chat")

        _MAX_PLANNER_HISTORY  = _planner_cfg.get("max_history_messages", 6)
        _MAX_MSG_CHARS        = _planner_cfg.get("max_msg_chars", 2000)
        _MAX_RESULT_SNIPPET   = _planner_cfg.get("max_result_snippet", 2000)
        _MAX_CHAT_HISTORY     = _chat_cfg.get("max_history_messages", 20)
        _MAX_CHAT_MSG_CHARS   = _chat_cfg.get("max_msg_chars", 4000)

        full_history = await self._get_history()
        raw_history = full_history[:-1] if full_history else []
        history_for_planner = [
            {"role": m["role"], "content": m["content"][:_MAX_MSG_CHARS] + ("..." if len(m["content"]) > _MAX_MSG_CHARS else "")}
            for m in raw_history[-_MAX_PLANNER_HISTORY:]
        ]

        # Inject structured recent tool results as a clean context block.
        if self._last_tool_results:
            recent_block_lines = ["RECENT TOOL RESULTS (use values as literal arguments — NEVER reference these as a depends_on target):"]
            for r in self._last_tool_results:
                snippet = r["result"][:_MAX_RESULT_SNIPPET] + ("..." if len(r["result"]) > _MAX_RESULT_SNIPPET else "")
                recent_block_lines.append(f"- {r['tool']} output: {snippet}")
            recent_block = "\n".join(recent_block_lines)
            history_for_planner = [{"role": "system", "content": recent_block}] + history_for_planner

        # ── Mode Branching ─────────────────────────────
        if mode == "chat":
            # Chat Mode does no LLM-driven tool calling at all — that's Agent
            # Mode's job exclusively (a deliberate product decision: one
            # clear place tools execute, with the plan/confirm/execute
            # ceremony and the grounding checks that come with it, rather
            # than two divergent tool paths of different rigor). A URL is
            # still detected — a cheap deterministic regex check, not the
            # LLM choosing to call a tool — but instead of fetching it, Chat
            # Mode nudges the user to Agent Mode rather than silently
            # ignoring an obvious intent. Export-to-file gets the same
            # deterministic treatment below, but is actually executed here
            # instead of just nudged: Agent Mode's planner has proven
            # unreliable specifically for "convert what I just read to a
            # file" (it tends to invent a redundant fetch step even when the
            # content is already in front of it), while Chat Mode already
            # has the document content and just wrote the answer/summary —
            # exporting it is a fixed, no-choices-to-hallucinate function
            # call, not a plan the LLM has to design.
            url = self._extract_url(message)
            if url:
                nudge = (
                    f"I can't open web pages in Chat Mode — switch to **Agent Mode** "
                    f"(the toggle below) and ask me again to have me look at {url}."
                )
                await self._append_history("assistant", nudge)
                return nudge

            # 1. RAG Retrieval for Uploaded Documents
            document_context = await self._get_document_context(message, attachments, status_callback)

            if status_callback:
                await status_callback("Generating...")

            # 2. Skills — Chat Mode only (see app/core/skills.py for why: no
            # tool loop here to hang script execution off, so skills stay
            # pure instructional guidance). Metadata for every installed
            # skill is cheap enough to always include; only skills whose
            # description matches this message get their full body injected.
            try:
                from app.core.skills import (
                    load_skills, build_skills_metadata_block,
                    match_skills, build_triggered_skills_block,
                )
                _all_skills = load_skills()
                from app.db.crud import is_capability_active
                _cap_db = SessionLocal()
                try:
                    _skills = [
                        s for s in _all_skills
                        if is_capability_active(_cap_db, self.connection_id, "skill", s.folder)
                    ]
                finally:
                    _cap_db.close()
                skills_metadata = build_skills_metadata_block(_skills)
                triggered_skills_block = build_triggered_skills_block(match_skills(message, _skills))
            except Exception as e:
                logger.warning(f"Skills load failed: {e}")
                skills_metadata = ""
                triggered_skills_block = ""

            # Detected before generation (not after, like everything else
            # here) so that when it's a hit, the model can be told — for
            # *this* turn only — to fence the exact exportable content
            # instead of us trying to regex-clean whatever it freely wrote
            # after the fact. Denylisting bad phrasing (self-doubt, fake
            # tool narration) is inherently a losing game against a small
            # model that can phrase either in unbounded ways; a positive,
            # structural instruction ("put the exact content in this
            # fence") only needs the model to get the good case right once,
            # and _clean_export_content still runs as a fallback/second
            # pass below either way.
            import asyncio
            loop = asyncio.get_running_loop()
            # An explicit format picked from the composer's "+" > Export menu
            # skips export classification entirely — the user already told
            # us the intent, so there's nothing left to infer there. Compound-
            # question detection is independent of that, so when BOTH cheap
            # gates fire (still need to classify export AND the message looks
            # like a bundled multi-part question), one combined LLM call
            # answers both instead of two serialized ones — see
            # _classify_export_and_compound's docstring for why this only
            # applies when both are actually in play.
            need_export_classification = (
                export_format not in ("pdf", "docx", "xlsx") and bool(self._EXPORT_HINT_RE.search(message))
            )
            need_compound_classification = bool(
                self._COMPOUND_QUESTION_MARK_RE.search(message) and self._COMPOUND_CONNECTOR_RE.search(message)
            )

            if export_format in ("pdf", "docx", "xlsx"):
                export_fmt = export_format
                compound_parts = (
                    await loop.run_in_executor(llm_executor, self._decompose_compound_question, message)
                    if need_compound_classification else None
                )
            elif need_export_classification and need_compound_classification:
                export_fmt, compound_parts = await loop.run_in_executor(
                    llm_executor, self._classify_export_and_compound, message
                )
            elif need_export_classification:
                export_fmt = await loop.run_in_executor(llm_executor, self._classify_export_intent, message)
                compound_parts = None
            elif need_compound_classification:
                export_fmt = None
                compound_parts = await loop.run_in_executor(llm_executor, self._decompose_compound_question, message)
            else:
                export_fmt = None
                compound_parts = None

            # Append document context to the base entity context
            full_context = entity_context
            if document_context:
                full_context += "\n" + document_context
            if skills_metadata:
                full_context += "\n\n" + skills_metadata
            if triggered_skills_block:
                full_context += "\n\n" + triggered_skills_block
            if compound_parts:
                full_context += (
                    "\n\n[MULTI-PART QUESTION]: The user asked multiple distinct things in "
                    "one message. Address EACH of the following separately and completely "
                    "in your answer — do not skip any:\n"
                    + "\n".join(f"{i+1}. {p}" for i, p in enumerate(compound_parts))
                )
            if export_fmt:
                full_context += (
                    f"\n\n[EXPORT INSTRUCTION]: The user also wants this turn's content "
                    f"exported as a {export_fmt.upper()} file — that happens automatically "
                    f"right after you answer, no tool call needed from you. Write your "
                    f"answer as usual, and additionally wrap ONLY the exact final content "
                    f"that should go into the exported file in a fenced block tagged "
                    f"\"export\", e.g.:\n```export\n<the exact content to export, nothing "
                    f"else>\n```\nPut just the clean final content there — no meta-commentary, "
                    f"no mention of tools, modes, or capabilities, no apologies. If your "
                    f"answer already IS the exportable content (a summary, a tagline, a "
                    f"table), the fence can just repeat that same text."
                )

            all_tools_str = self.get_available_tools()
            chat_prompt = build_chat_prompt(full_context, all_tools_str)
            logger.info("Generated Chat Prompt successfully.")
            
            messages = [{"role": "system", "content": chat_prompt}]
            
            import re
            # Apply Chat history cap and per-message char cap from the live config.
            capped_history = [
                {"role": m["role"], "content": m["content"][:_MAX_CHAT_MSG_CHARS] + ("..." if len(m["content"]) > _MAX_CHAT_MSG_CHARS else "")}
                for m in full_history[-_MAX_CHAT_HISTORY:]
            ]
            sanitized_history = []
            for msg in capped_history:
                content = msg["content"]
                if msg["role"] == "assistant" and "Proposed Execution Plan" in content:
                    content = content.replace("**Proposed Execution Plan:**", "**Past Action Plan:**")
                    content = content.replace("Proposed Execution Plan:", "Past Action Plan:")
                    content = re.sub(r'\n```json\n[\s\S]*?\n```\n\n', '', content)
                    content = content.replace("Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)", "")
                    content = content.replace("Would you like me to proceed with this? (Reply 'yes' to execute or tell me what to edit)", "")
                sanitized_history.append({"role": msg["role"], "content": content})
                
            messages.extend(sanitized_history)
            self._attach_vision_images(messages, attachments)

            # In chat mode, we expect pure raw text, no JSON.
            import asyncio
            loop = asyncio.get_running_loop()
            chat_response = await loop.run_in_executor(
                llm_executor,
                lambda: self._call_llm_text(messages, token_callback)
            )

            if export_fmt:
                # Prefer the structural fence the prompt above asked for —
                # free (no extra LLM call) when the model actually complies.
                # When it doesn't, fall back to a second small LLM call
                # (_extract_export_content_via_llm) whose only job is
                # extracting the clean content, rather than just the
                # denylist alone — that call can strip ANY kind of
                # surrounding noise (narration, self-doubt, redundant
                # restatement), not just phrasings that happen to match a
                # known-bad pattern. Either way this replaces chat_response
                # itself (not just a copy for the export payload) — a
                # degraded small model sometimes tacks on hallucinated tool
                # narration or a fake "[Download ...]" link of its own
                # after the real answer, and while we can't un-stream
                # tokens already sent live for *this* turn, this keeps
                # persisted history (and future turns' context — otherwise
                # the model starts imitating its own fake link pattern)
                # clean, and prevents a bogus second link from sitting next
                # to the one real link we're about to add.
                fence_match = re.search(r'```export\s*\n(.*?)```', chat_response, re.DOTALL | re.IGNORECASE)
                if fence_match:
                    export_content = self._clean_export_content(fence_match.group(1).strip())
                    chat_response = (
                        chat_response[:fence_match.start()] + export_content + chat_response[fence_match.end():]
                    ).strip()
                else:
                    export_content = await loop.run_in_executor(
                        llm_executor,
                        lambda: self._extract_export_content_via_llm(chat_response, export_fmt)
                    )
                    chat_response = export_content
                export_result = await self._execute_export_document({
                    "content": export_content,
                    "format": export_fmt,
                })
                if export_result.get("success"):
                    extra = f"\n\n[Download {export_result['filename']}]({export_result['download_url']})"
                else:
                    extra = f"\n\n*(Couldn't export that as {export_fmt}: {export_result.get('error')})*"
                chat_response += extra
                # The response above this point was already streamed
                # token-by-token; the caller only resends the full return
                # value when nothing was streamed. Push the appended link
                # through the same live channel so it isn't silently dropped.
                if token_callback:
                    token_callback(extra)

            await self._append_history("assistant", chat_response)

            return chat_response

        # If mode == "agent", we skip the Chat LLM and go straight to Plan Generation.
        # Export-to-file no longer has a tool here at all (see
        # _get_local_tools' docstring) — it moved to Chat Mode's
        # deterministic handling because Agent Mode's planner proved
        # unreliable at it. Mirrors Chat Mode's own URL-detection nudge
        # above, just in the opposite direction: a light LLM classification
        # pass (see _classify_export_intent), not the planner discovering
        # mid-plan that no tool fits.
        import asyncio
        loop = asyncio.get_running_loop()
        if export_format in ("pdf", "docx", "xlsx"):
            export_fmt = export_format
        else:
            export_fmt = await loop.run_in_executor(llm_executor, self._classify_export_intent, message)
        if export_fmt:
            nudge = (
                "Exporting to a file is handled in **Chat Mode** — switch to it "
                "(the toggle below) and ask me again there."
            )
            await self._append_history("assistant", nudge)
            return nudge

        # RAG context on already-uploaded documents — without this the
        # planner has no way to know a document exists at all and will
        # hallucinate a URL/tool for "the report I uploaded earlier" instead
        # of just using its content (e.g. to feed a Gmail/Slack send tool).
        document_context = await self._get_document_context(message, attachments, status_callback)
        planner_context = entity_context + ("\n\n" + document_context if document_context else "")

        import asyncio
        loop = asyncio.get_running_loop()
        # db_executor, not llm_executor — get_searched_tools does no LLM
        # work anymore (see its docstring), so routing it through the
        # single-worker LLM queue would just make it wait behind unrelated
        # generation calls for no reason.
        tools_str, is_counting, offered_tool_names = await loop.run_in_executor(db_executor, self.get_searched_tools, message)
        if not tools_str:
            return (
                "I don't have any connected tools relevant to that request. "
                "Check **Connectors** to add the right one, or **Marketplace** to see what's available."
            )

        # ── Plan Generation & Self-Correction Loop ────────────────────────────
        import jsonschema
        all_tools = self._all_available_tools()
        valid_tool_names = {t["name"] for t in all_tools}
        tool_schemas = {t["name"]: t.get("inputSchema", {}) for t in all_tools}

        if status_callback:
            await status_callback("Drafting execution plan...")

        import asyncio
        loop = asyncio.get_running_loop()

        # Up to one retry, with a reinforced instruction, before giving up —
        # covers three distinct small-model failure modes seen in practice,
        # all previously either a hard immediate failure or a silently
        # accepted bad result:
        #   1. Invalid JSON (often a runaway plan that overflowed max_tokens
        #      mid-generation — e.g. a degenerate "browser_extract_text at
        #      offset 0, 1000, 2000, ..." pattern with 15+ steps).
        #   2. A plan that DID parse but is absurdly long — this app's
        #      tools never legitimately need more than a handful of steps,
        #      so anything over _MAX_RAW_PLAN_STEPS is almost certainly the
        #      same runaway pattern, just short enough to fit in the token
        #      budget this time.
        #   3. Neither a plan nor a direct_response/clarifying_question —
        #      the planner prompt explicitly forbids this (see its
        #      capability-question rule) but a degraded local model can
        #      still produce it.
        # _MAX_PLAN_ATTEMPTS=2 keeps the added latency bounded to a single
        # extra generation call, and only for the attempts that actually
        # need it — a normal, well-formed plan exits the loop on try 1.
        _MAX_PLAN_ATTEMPTS = 2
        _MAX_RAW_PLAN_STEPS = 8

        # Held back for the repetition-guard retry below, used INSTEAD of
        # history_for_planner on that one retry — not just the "RECENT TOOL
        # RESULTS" block, but empty entirely. Verified empirically that the
        # weaker version of this fix (stripping only that one system block,
        # keeping the actual conversation turns) still reproduced the
        # IDENTICAL wrong plan byte-for-byte: the model's own prior
        # "Proposed Execution Plan" text sitting in ordinary history is
        # apparently just as strong an anchor as the synthetic tool-results
        # block was. The point of this retry is specifically "reconsider
        # this request independently of whatever just happened" — so for
        # this one attempt, "independently" means genuinely no prior
        # turns, not a lighter version of the same contaminated context.
        plan_data = None
        retry_note = ""
        use_stripped_history = False
        for attempt in range(_MAX_PLAN_ATTEMPTS):
            augmented_message = message + retry_note
            attempt_history = [] if use_stripped_history else history_for_planner
            # Diagnostic: planner_context (entity memory + RAG document
            # context) is the one input NOT yet cleared on the repetition
            # retry — history-clearing alone didn't stop the repeat, so
            # this checks whether entity memory is the real anchor instead.
            attempt_context = "" if use_stripped_history else planner_context
            plan_json_str = await loop.run_in_executor(
                llm_executor,
                lambda am=augmented_message, ah=attempt_history, ac=attempt_context: self.planner.generate_plan(
                    am, tools_str, ac, ah,
                    token_callback=None, is_counting=is_counting, tool_names=offered_tool_names,
                    attachments=attachments,
                )
            )
            last_attempt = attempt == _MAX_PLAN_ATTEMPTS - 1

            try:
                candidate_data = json.loads(plan_json_str, strict=False)
            except json.JSONDecodeError:
                if last_attempt:
                    break
                retry_note = (
                    "\n\n[SYSTEM]: Your previous response could not be parsed as JSON. "
                    "Respond with ONLY a single valid JSON object — no text before or "
                    "after it — and keep the plan to at most "
                    f"{_MAX_RAW_PLAN_STEPS} steps."
                )
                continue

            candidate_raw = candidate_data if isinstance(candidate_data, list) else candidate_data.get("plan", [])
            candidate_raw = [
                s for s in candidate_raw
                if isinstance(s, dict) and s.get("tool")
                and str(s.get("tool")).lower() not in ("none", "none_available", "null", "n/a", "unknown")
            ]

            if len(candidate_raw) > _MAX_RAW_PLAN_STEPS and not last_attempt:
                logger.warning(f"[PlannerAgent] Oversized plan ({len(candidate_raw)} steps) — retrying with a shorter-plan instruction.")
                retry_note = (
                    f"\n\n[SYSTEM]: Your previous plan had {len(candidate_raw)} steps, which "
                    "is far too many — you likely got stuck repeating a pattern. This app's "
                    f"tools never legitimately need more than {_MAX_RAW_PLAN_STEPS}. Generate "
                    "a SHORT plan that directly accomplishes the request, or return an empty "
                    "plan with a clarifying_question if the request is unclear."
                )
                continue

            has_direct_response = isinstance(candidate_data, dict) and bool(candidate_data.get("direct_response"))
            has_clarifying_question = isinstance(candidate_data, dict) and bool(candidate_data.get("clarifying_question"))
            if not candidate_raw and not has_direct_response and not has_clarifying_question and not last_attempt:
                retry_note = (
                    "\n\n[SYSTEM]: Your previous response had neither a \"plan\" nor a "
                    "\"direct_response\". You MUST provide one of the two — if no tool call "
                    "is needed, answer the user directly in \"direct_response\" instead of "
                    "leaving everything empty."
                )
                continue

            # 4. Suspicious repetition: this exact (tool, arguments) set was
            # already proposed last turn, but nothing in the CURRENT
            # message references any of its argument values — a degraded
            # small model anchoring on whatever tool call it just saw
            # succeed and echoing it verbatim, rather than reasoning about
            # a genuinely different new request (reproduced: after a
            # successful search_local_files(query="budget"), "what's
            # inside this folder?" got the identical search_local_files
            # plan back instead of list_folder). Argument-value overlap is
            # the escape hatch for a real "do that again" follow-up, which
            # should NOT be blocked.
            if (
                candidate_raw
                and self._last_proposed_plan_signature is not None
                and self._plan_signature(candidate_raw) == self._last_proposed_plan_signature
                and not last_attempt
            ):
                msg_lower = message.lower()
                arg_values = [
                    str(v).lower() for step in candidate_raw
                    for v in (step.get("arguments") or {}).values()
                    if isinstance(v, (str, int, float)) and len(str(v)) > 2
                ]
                if not any(v in msg_lower for v in arg_values):
                    logger.warning("[PlannerAgent] Plan identical to the last one proposed, with no argument overlap in the new message — retrying with the recent-tool-results anchor removed.")
                    retry_note = (
                        "\n\n[SYSTEM]: The plan you just generated is IDENTICAL to the one "
                        "you already proposed for a DIFFERENT, previous request — you appear "
                        "to be repeating it instead of addressing what's being asked now. "
                        "Re-read the user's CURRENT message above carefully and generate a "
                        "plan that specifically addresses THAT request. If it genuinely needs "
                        "no tool, return an empty plan with a direct_response instead."
                    )
                    use_stripped_history = True
                    continue

            plan_data = candidate_data
            break

        if plan_data is None:
            # Exhausted every attempt without ever getting parseable JSON —
            # distinct from the "parsed fine but still degenerate" case
            # below, which at least has a plan_data to fall back on.
            self.state = AgentState.IDLE
            self.plan = None
            return (
                "I couldn't put together a plan for that after a couple of tries — the "
                "request might be more complex than I can currently handle, or ambiguous. "
                "Try breaking it into a simpler, more specific request."
            )

        if isinstance(plan_data, list):
            raw_plan = plan_data
        else:
            raw_plan = plan_data.get("plan", [])
            
        # Filter out placeholder tools that the LLM might hallucinate when no tools are needed
        valid_plan = []
        for step in raw_plan:
            if isinstance(step, dict) and step.get("tool"):
                tool_name = str(step.get("tool")).lower()
                if tool_name not in ("none", "none_available", "null", "n/a", "unknown"):
                    valid_plan.append(step)
        raw_plan = valid_plan

        # Handle clarification escape hatch ONLY if no valid plan steps were generated
        if not raw_plan and isinstance(plan_data, dict) and plan_data.get("clarifying_question"):
            question = _sanitize_one_shot_text(
                plan_data.get("clarifying_question"),
                fallback="Could you clarify what you'd like me to do?",
            )
            self.state = AgentState.IDLE
            await self._append_history("assistant", question)

            return question
        # Basic validation
        validation_errors = []
        bad_tool_name = None
        for step in raw_plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tool_names:
                validation_errors.append(f"Tool `{tool_name}` does not exist.")
                bad_tool_name = bad_tool_name or tool_name

        if validation_errors:
            self.state = AgentState.IDLE
            return (
                f"I don't have a tool for that (`{bad_tool_name}`) — it's not something I "
                "can do yet. Check **Marketplace** for what's available, or try rephrasing "
                "your request."
            )
        
        # 2. Metadata Validation (Dependencies, IDs)
        self.plan = []
        
        if isinstance(plan_data, dict):
            warnings = plan_data.get("warnings", [])
        else:
            warnings = []
            
        if isinstance(warnings, str):
            warnings = [warnings]

        # Fix 1: Increment the session-wide turn counter and rewrite all step IDs
        # from the LLM (e.g. "step_1") to globally unique IDs (e.g. "t3_step_1").
        # This makes it structurally impossible for the Planner to form a valid
        # depends_on reference to a step from a previous turn, since old step IDs
        # (e.g. "t1_step_1") will never appear in all_step_ids for this new plan.
        self._turn_counter += 1
        turn_prefix = f"t{self._turn_counter}"

        # Rewrite step IDs with turn prefix before validation
        id_remap: Dict[str, str] = {}  # old_id -> new_id
        for step in raw_plan:
            old_id = step.get("step_id")
            if old_id:
                new_id = f"{turn_prefix}_{old_id}"
                id_remap[old_id] = new_id
                step["step_id"] = new_id

        # Also rewrite depends_on references using the same map
        for step in raw_plan:
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list):
                step["depends_on"] = [id_remap.get(did, did) for did in depends_on]

        # First pass: collect all declared step_ids IN THIS PLAN ONLY
        all_step_ids = {step.get("step_id") for step in raw_plan if step.get("step_id")}
        
        for step in raw_plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tool_names:
                continue # Already caught above, but safe to skip
            
            # Validate depends_on — must only reference steps in the current plan.
            # If the LLM hallucinated a cross-turn stale step reference (e.g. "step_1" from
            # a prior turn), strip it to [] with a warning rather than aborting the whole plan.
            # The correct value for the argument is available in the RECENT TOOL RESULTS block.
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list):
                valid_deps = []
                for did in depends_on:
                    if did in all_step_ids:
                        valid_deps.append(did)
                    else:
                        logger.warning(
                            f"Stripped stale/cross-turn depends_on '{did}' "
                            f"from step '{step.get('step_id')}' — not in current plan."
                        )
                step["depends_on"] = valid_deps
            else:
                step["depends_on"] = []
                
            # Validate foreach target
            foreach_target = step.get("foreach")
            if foreach_target and foreach_target not in all_step_ids:
                step["foreach"] = None
                
            # Ensure every step has an ID (fallback for steps that had no step_id at all)
            if not step.get("step_id"):
                import uuid
                step["step_id"] = f"{turn_prefix}_step_{str(uuid.uuid4())[:8]}"
                all_step_ids.add(step["step_id"])
                
            self.plan.append(step)

        if not self.plan:
            self.state = AgentState.IDLE
            direct_response = plan_data.get("direct_response") if isinstance(plan_data, dict) else None
            if direct_response:
                response = _sanitize_one_shot_text(
                    direct_response,
                    fallback="I'm not sure how to answer that directly — could you rephrase it?",
                )
            elif warnings:
                response = "**Note:**\n" + "\n".join([f"- {w}" for w in warnings])
            else:
                # The model returned neither a plan nor a direct_response on
                # ANY attempt (the generation loop above already gave it one
                # reinforced retry) — a degenerate output the planner prompt
                # explicitly forbids, but a sufficiently degraded local
                # model can still produce it twice in a row. Rather than
                # showing a bare tool-list dump that doesn't answer what was
                # actually asked, fall back to a plain-text answer the same
                # way Chat Mode would — the user gets a real response
                # instead of "what would you like me to do?" to a question
                # they already asked clearly. The tool-list dump is kept
                # only as the last-resort fallback if even this fails.
                fallback_messages = [{"role": "system", "content": build_chat_prompt(entity_context)}]
                fallback_messages.extend(history_for_planner)
                fallback_messages.append({"role": "user", "content": message})
                self._attach_vision_images(fallback_messages, attachments)
                response = await loop.run_in_executor(
                    llm_executor,
                    lambda: self._call_llm_text(fallback_messages, None)
                )
                if not response or not response.strip():
                    tool_names = sorted({t["name"] for t in self._all_available_tools()})
                    names_str = ", ".join(f"`{n}`" for n in tool_names) if tool_names else "no tools"
                    response = f"I have access to: {names_str}. What would you like me to do?"

            await self._append_history("assistant", response)
                
            return response

        self.state = AgentState.WAITING_CONFIRMATION
        # Recorded for the NEXT turn's repetition guard (see the retry loop
        # above) — deliberately not cleared on cancel, since a cancelled
        # plan being immediately repeated verbatim is just as suspicious as
        # an executed one being repeated.
        self._last_proposed_plan_signature = self._plan_signature(self.plan)

        response = "**Proposed Execution Plan:**\n\n"
        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            scope = step.get("fetch_scope", "single")
            # Tells the user, at a glance, which steps only look at data and
            # which ones actually change something, before they hand out one
            # blanket "yes" for the whole plan. A small hardcoded set
            # (_READ_ONLY_TOOL_NAMES) replaces the old generic
            # MCP-tool-registry classifier — everything else (browser_*,
            # write_file) is treated as a write.
            action_badge = "read-only" if tool_name in self._READ_ONLY_TOOL_NAMES else "writes"
            response += f"**Step {i+1}: `{tool_name}`** `[{action_badge}]` `[scope: {scope}]`\n"
            reason = _sanitize_one_shot_text(step.get("reason") or "", fallback="")
            if reason:
                response += f"> {reason}\n"

            arguments = step.get("arguments")
            if arguments:
                response += f"- *Arguments:* `{json.dumps(arguments, ensure_ascii=False)}`\n"

            depends = step.get("depends_on")
            if depends:
                response += f"- *Depends on:* {', '.join(depends)}\n"
            response += "\n"

        if warnings:
            response += "**Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"

        response += "Would you like me to proceed with this? (Reply **'yes'** to execute or tell me what to edit)"
        
        await self._append_history("assistant", response)

        # Cache the plan response so it can be replayed on reconnect if the client
        # dropped its WebSocket during LLM inference (e.g. React Strict Mode remount).
        self._pending_response = response

        return response

    @staticmethod
    def _format_tool_results_markdown(heading: str, tool_results: List[Dict]) -> str:
        """
        Render finished tool_results as clean per-tool markdown — reusing the
        exact same "display" text already shown live in each step's
        step_result card — instead of a raw JSON dump. This is stored to chat
        history and tagged msg_type='tool_call' so it replays into the
        collapsed "Agent is working" card on reload, staying consistent with
        what was shown live: the LLM still gets the full data via `result`
        in the planner/executor context block, but a human never sees a bare
        JSON blob here.
        """
        parts = [f"**{heading}**"]
        for r in tool_results:
            tool_name = r["tool"]
            display = r.get("display")
            if not display:
                # Fallback for entries built without a "display" key (e.g.
                # the skipped-step / exception branches) — best-effort
                # readable text.
                try:
                    parsed = json.loads(r["result"])
                    display = json.dumps(parsed, indent=2)[:800]
                except (json.JSONDecodeError, TypeError):
                    display = str(r.get("result", ""))[:800]
            parts.append(f"\n**`{tool_name}`**\n{display}")
        return "\n".join(parts)

    async def _handle_confirmation(self, message: str, token_callback=None) -> str:
        """WAITING_CONFIRMATION → confirm → EXECUTING  or  refine plan."""
        # New-task pivot — checked BEFORE appending to history, so a genuine
        # pivot can cleanly hand off to _handle_idle (which does its own
        # append) without double-recording the user's message. A user who
        # ignores a pending plan and asks about a clearly different URL or
        # file path than anything already in that plan has moved on to a
        # new task, not editing this one — previously that got force-fit
        # through the "refine the plan" path below, mixing stale plan
        # context with the new request and sometimes producing raw,
        # unformatted JSON instead of a real answer (reproduced with e.g. a
        # pending web_scrape plan, then "save X to notes/review.txt" —  no
        # URL, so only a path-aware check catches it).
        new_resource = self._extract_url(message) or self._extract_path_like(message)
        if new_resource and not any(
            new_resource in json.dumps(step.get("arguments", {})) for step in (self.plan or [])
        ):
            self.plan = []
            self.state = AgentState.IDLE
            return await self._handle_idle(message, mode="agent", token_callback=token_callback)

        # Plan was seen and acted on by the user — clear the reconnect cache.
        self._pending_response = None
        await self._append_history("user", message)


        positive_keywords = ['yes', 'proceed', 'go ahead', 'do it', 'sure', 'ok', 'okay', 'yep', 'yeah', 'looks good']
        is_positive = self._matches_any_keyword(message, positive_keywords)

        if is_positive and len(message.split()) < 10:
            self.state = AgentState.EXECUTING
            return "Great! Proceeding with the execution... (Please wait)"

        cancel_keywords = ['cancel', 'abort', 'stop', 'nevermind']
        if self._matches_any_keyword(message, cancel_keywords):
            self.state = AgentState.IDLE
            self.plan = []
            return "Plan cancelled. What would you like to do next?"
            
        else:
            # ── Plan Refinement Loop: Questions vs Edits ─────────────────────
            msg_lower = message.lower().strip()
            edit_verbs = ['change', 'update', 'use', 'make', 'edit', 'add', 'remove', 'instead', 'no', 'dont', 'do not']
            is_question = "?" in msg_lower and not any(verb in msg_lower for verb in edit_verbs)

            if is_question:
                entity_context = self._get_entity_context()
                from app.prompts.chat import build_chat_prompt
                chat_prompt = build_chat_prompt(entity_context)
                
                system_injection = (
                    f"\n\n[SYSTEM]: The user has a pending plan they are reviewing. "
                    f"The current plan is: {json.dumps(self.plan)}. "
                    f"Answer their question about the plan conversationally. Do NOT execute it."
                )
                
                messages = [{"role": "system", "content": chat_prompt + system_injection}]
                full_history = await self._get_history()
                messages.extend(full_history)
                import asyncio
                loop = asyncio.get_running_loop()
                response_text = await loop.run_in_executor(
                    llm_executor,
                    lambda: self._call_llm_text(messages, token_callback)
                )
                final_response = response_text + "\n\n*(Plan is still pending. Reply 'yes' to execute or tell me what to change)*"
                await self._append_history("assistant", final_response)
                
                return final_response

            # Otherwise, treat as an edit request and route to Planner
            tools_str, is_counting, offered_tool_names = self.get_searched_tools(message)
            if not tools_str:
                return "I couldn't find any tools relevant to that edit request. Please clarify what you want to do."

            entity_context = self._get_entity_context()
            import asyncio
            loop = asyncio.get_running_loop()
            full_history = await self._get_history()
            plan_json_str = await loop.run_in_executor(
                llm_executor,
                lambda: self.planner.generate_plan(
                    "Please refine the plan based on my previous feedback.",
                    tools_str, entity_context, full_history, token_callback, is_counting,
                    tool_names=offered_tool_names,
                )
            )
            try:
                plan_data = json.loads(plan_json_str)
                raw_refined = plan_data if isinstance(plan_data, list) else plan_data.get("plan", [])

                # Apply the same turn-prefix rewriting as the main plan path so that
                # step IDs are globally unique and cross-turn depends_on refs are stripped.
                self._turn_counter += 1
                turn_prefix = f"t{self._turn_counter}"
                id_remap: Dict[str, str] = {}
                for step in raw_refined:
                    old_id = step.get("step_id")
                    if old_id:
                        new_id = f"{turn_prefix}_{old_id}"
                        id_remap[old_id] = new_id
                        step["step_id"] = new_id

                for step in raw_refined:
                    depends_on = step.get("depends_on")
                    if isinstance(depends_on, list):
                        step["depends_on"] = [id_remap.get(did, did) for did in depends_on]

                all_step_ids = {s.get("step_id") for s in raw_refined if s.get("step_id")}
                for step in raw_refined:
                    if isinstance(step.get("depends_on"), list):
                        step["depends_on"] = [did for did in step["depends_on"] if did in all_step_ids]
                    else:
                        step["depends_on"] = []

                self.plan = raw_refined

                response = "I have refined the execution plan:\n\n"
                for i, step in enumerate(self.plan):
                    reason = _sanitize_one_shot_text(step.get("reason") or "", fallback="")
                    response += f"{i+1}. **{step.get('tool')}**: {reason}\n"
                response += "\nIs this better? (Reply 'yes' to proceed)"
                
                await self._append_history("assistant", response)
                    
                return response
            except Exception as e:
                # Broadened from json.JSONDecodeError: a malformed/unexpected
                # shape here (e.g. plan_data.get on something that isn't a
                # dict) previously propagated out as a raw, unformatted
                # error or — worse — let the model's raw JSON text leak
                # straight through as the "response". Whatever the cause,
                # the pending plan is still intact — never worse than
                # telling the user that and letting them retry.
                logger.warning(f"Plan refinement failed: {e}")
                return (
                    "I couldn't refine the plan from that. The original plan is still "
                    "pending — reply 'yes' to run it, 'cancel' to discard it, or try "
                    "describing your edit differently."
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Plan execution
    # ─────────────────────────────────────────────────────────────────────────

    async def execute_plan(self, token_callback=None) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes the approved plan step by step, then proposes entities to remember."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield {"text": "No plan to execute.", "node_id": None}
            return

        all_tools = self._all_available_tools()
        if not all_tools:
            yield {"text": "Error: No active local tools found.", "node_id": None}
            self.state = AgentState.IDLE
            return

        tool_schemas = {t["name"]: t.get("inputSchema", {}) for t in all_tools}
        import jsonschema
        import traceback

        # Retrieve context for the ExecutorAgent
        # Trim chat history to last 6 messages, capped at 600 chars each, to keep the executor
        # within the local LLM context window.
        _MAX_EXEC_HISTORY = 6
        _MAX_EXEC_CHARS = 600
        full_history = await self._get_history()
        trimmed_history = [
            {"role": m["role"], "content": m["content"][:_MAX_EXEC_CHARS] + ("..." if len(m["content"]) > _MAX_EXEC_CHARS else "")}
            for m in full_history[-_MAX_EXEC_HISTORY:]
        ]
        full_chat_history = json.dumps(trimmed_history, indent=2)
        entity_context = self._get_entity_context()

        # Run each tool step and collect raw results
        tool_results: List[Dict] = []
        # Stores structured output per step_id for the Executor — avoids prose-parsing for IDs.
        # Format: {node_id: {"tool": tool_name, "output": <parsed JSON or raw string>}}
        prior_results_map: Dict[str, Any] = {}
        total_steps = len(self.plan)
        # step_ids skipped because a step they (transitively) depend on
        # failed — populated by the exception handler below. A failure no
        # longer aborts the whole plan; only the failed step and whatever
        # actually needs its output get skipped, so independent steps still
        # run (e.g. "check GitHub and check email" doesn't lose the email
        # half just because GitHub had a bad API day).
        skipped_step_ids: set = set()

        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            node_id = step.get("step_id")
            step_reason = step.get("reason", "")

            if node_id in skipped_step_ids:
                skip_msg = f"Skipping Step {i+1} (`{tool_name}`) — depends on a step that failed.\n"
                yield {"text": skip_msg, "node_id": node_id, "status": "failed"}
                skip_error = "Skipped — depends on a step that failed."
                prior_results_map[node_id] = {"tool": tool_name, "output": {"error": skip_error}}
                tool_results.append({
                    "tool": tool_name,
                    "arguments": step.get("arguments", {}),
                    "result": json.dumps({"error": skip_error}),
                    "display": f"**`{tool_name}`** — {skip_error}",
                    "auto_paginated": False,
                    "cap_hit": False,
                    "node_id": node_id,
                })
                continue

            yield {"text": f"\nExecuting Task {i+1}/{total_steps}: Calling `{tool_name}`...\n", "node_id": node_id, "status": "running"}

            schema = tool_schemas.get(tool_name, {})
            if not schema:
                yield {"text": f"Plan aborted: Tool `{tool_name}` no longer exists.\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

            yield {"text": f"Generating exact parameters for `{tool_name}`...\n", "node_id": node_id, "status": "running"}

            # Serialize structured prior results as a JSON array for the Executor.
            # shape_for_executor has already produced compact, size-bounded dicts so
            # the Executor LLM context window is never blown out by large API payloads.
            prior_results_for_executor = [
                {
                    "step_id": sid,
                    "tool": v["tool"],
                    "output": v["output"],  # already shaped — compact dict
                }
                for sid, v in prior_results_map.items()
            ]

            # Generate arguments live using the deterministic Executor Agent.
            # Up to one retry with a reinforced instruction on invalid
            # output before aborting the plan — mirrors the planner's own
            # retry loop above (_MAX_PLAN_ATTEMPTS): a degraded local model
            # producing one malformed arguments object is exactly as
            # recoverable-with-a-nudge as it producing one malformed plan,
            # so there's no reason the planner gets a second try and the
            # executor doesn't. Bounded to 2 attempts total, same as the
            # planner, so the added latency only hits the (rare) attempts
            # that actually need it.
            import asyncio
            loop = asyncio.get_running_loop()
            _MAX_ARG_ATTEMPTS = 2
            arguments = None
            validation_error = None
            for arg_attempt in range(_MAX_ARG_ATTEMPTS):
                retry_note = ""
                if validation_error is not None:
                    retry_note = (
                        f"\n\n[SYSTEM]: Your previous attempt was invalid: {validation_error}. "
                        "Re-read the schema and output ONLY a JSON object matching it exactly."
                    )
                candidate = await loop.run_in_executor(
                    llm_executor,
                    lambda rn=retry_note: self.executor.generate_arguments(
                        tool_name=tool_name,
                        tool_schema=schema,
                        overall_plan=self.plan,
                        step_reason=step_reason,
                        prior_results=prior_results_for_executor,
                        entity_context=entity_context,
                        user_request=full_chat_history,
                        retry_note=rn,
                    )
                )
                last_attempt = arg_attempt == _MAX_ARG_ATTEMPTS - 1

                if isinstance(candidate, dict) and "error" in candidate:
                    validation_error = candidate["error"]
                    if last_attempt:
                        logger.error(f"Executor aborted for {tool_name}: {validation_error}")
                        yield {"text": f"Plan aborted: {validation_error}\n", "node_id": node_id, "status": "failed"}
                        self.state = AgentState.IDLE
                        self.plan = None
                        return
                    continue

                # Clean up known LLM hallucinations before validation
                if isinstance(candidate, dict):
                    # Small models often bleed the 'fetch_scope' step parameter into the arguments dict
                    if "fetch_scope" in candidate and "fetch_scope" not in schema.get("properties", {}):
                        del candidate["fetch_scope"]

                try:
                    jsonschema.validate(instance=candidate, schema=schema)
                except jsonschema.exceptions.ValidationError as e:
                    validation_error = e.message
                    if last_attempt:
                        logger.error(f"Executor failed schema validation for {tool_name}: {e.message}")
                        yield {"text": f"Plan aborted: Executor generated invalid arguments for `{tool_name}`: {e.message}\n", "node_id": node_id, "status": "failed"}
                        self.state = AgentState.IDLE
                        self.plan = None
                        return
                    continue

                arguments = candidate
                break

            # Semantic Grounding Check: Catch schema-valid but hallucinated IDs
            # Only validate ID-shaped arguments for steps that explicitly depend on previous outputs.
            depends_on = step.get("depends_on")
            if depends_on:
                dep_results = {dep_id: prior_results_map.get(dep_id) for dep_id in depends_on}
                
                # 1. Exact-match recursive search (prevents JSON-serialization false pos/neg)
                def _value_exists(needle, obj):
                    if isinstance(obj, dict):
                        return any(_value_exists(needle, v) for v in obj.values())
                    if isinstance(obj, list):
                        return any(_value_exists(needle, v) for v in obj)
                    return str(obj) == str(needle)

                # 2. Case-insensitive key matching (handles camelCase e.g., 'messageId', 'recordIDs')
                def _is_id_key(key: str) -> bool:
                    key_lower = key.lower()
                    if key_lower.endswith('ids'):
                        key_lower = key_lower[:-1]
                        key = key[:-1]
                    return key_lower == 'id' or key_lower.endswith('_id') or key.endswith('Id') or key.endswith('ID')

                grounding_errors = []
                
                for arg_k, arg_v in arguments.items():
                    if _is_id_key(arg_k):
                        # 3. Handle both single string IDs and arrays of IDs
                        items_to_check = arg_v if isinstance(arg_v, list) else [arg_v]
                        for item in items_to_check:
                            if isinstance(item, (str, int)) and len(str(item)) > 2:
                                # We accept the false-positive risk for mixed-source IDs rather than 
                                # reopening the substring/cross-turn vulnerability.
                                if not _value_exists(item, dep_results):
                                    grounding_errors.append(f"'{arg_k}'='{item}'")
                
                if grounding_errors:
                    err_msg = ", ".join(grounding_errors)
                    logger.error(f"Executor failed semantic grounding check for {tool_name}: {err_msg}")
                    
                    # 4. Partial progress reporting
                    success_msg = f"Successfully completed {i} prior steps. " if i > 0 else ""
                    yield {
                        "text": f"Plan aborted: {success_msg}Executor hallucinated fabricated IDs that don't exist in dependency outputs: {err_msg}\n", 
                        "node_id": node_id, 
                        "status": "failed"
                    }
                    self.state = AgentState.IDLE
                    self.plan = None
                    return

            try:
                from app.mcp.response_shapers import shape_for_executor, shape_accumulated_response

                # Every remaining tool is local (web_scrape / browser_* /
                # the sandboxed filesystem tools) — one action against one
                # page/session/path, no pagination loop needed (that whole
                # apparatus, cursor-following and all, existed purely for
                # MCP-connected list tools like Gmail/Notion/Drive/Slack,
                # none of which exist here anymore).
                yield {"text": f"Fetching `{tool_name}`…\n", "node_id": node_id, "status": "running"}

                if tool_name == "web_scrape":
                    outcome = await self._execute_web_scrape(arguments)
                elif tool_name in self._FILESYSTEM_TOOL_NAMES:
                    outcome = await self._execute_filesystem_tool(tool_name, arguments)
                else:
                    outcome = await self._execute_browser_action(tool_name, arguments)

                # A login-walled/private page still usually loads fine
                # (success=True with a partial/logged-out view, or a
                # generic "not found") — needs_auth is the real signal,
                # independent of success. Only web_scrape and
                # browser_navigate ever set it (every other browser_*
                # action's outcome simply won't have this key). No
                # retry path — public pages only, see
                # app.core.scraper's module docstring — so this just
                # becomes a note telling the model to say so, same as
                # any other tool outcome.
                if outcome["success"]:
                    if "text" in outcome:
                        # web_scrape / browser_navigate / browser_extract_text /
                        # read_file — all chunked identically via _chunk_text,
                        # capped there, so has_more/next_offset stay in sync
                        # with what text_preview actually holds. Keeps
                        # whatever extra metadata each tool's outcome carries
                        # (title+warnings for the web tools, path+file_type
                        # for read_file) generically rather than hardcoding
                        # one tool family's field names; drops the raw
                        # pagination bookkeeping in favor of the human-
                        # readable "note" below.
                        result = {
                            k: v for k, v in outcome.items()
                            if k not in ("success", "text", "offset", "next_offset", "has_more", "total_length")
                        }
                        result["text_preview"] = outcome["text"]
                        note = self._continuation_note(tool_name, outcome)
                        if note:
                            result["note"] = note
                    else:
                        # Every other browser_* action (click, fill, scroll,
                        # wait_for, screenshot, go_back, go_forward,
                        # list_tabs, switch_tab, close) — pass its own
                        # fields straight through.
                        result = {k: v for k, v in outcome.items() if k != "success"}
                else:
                    result = {"error": outcome.get("error")}
                    if outcome.get("needs_auth"):
                        result["note"] = (
                            "This page requires signing in or is private — this "
                            "tool only accesses public pages. Tell the user "
                            "directly that you can't access it."
                        )

                shaped_for_exec = shape_for_executor(tool_name, result)
                final_display = shape_accumulated_response(tool_name, [shaped_for_exec], 1, raw_items=[result])
                yield {
                    "type": "step_result",
                    "text": final_display,
                    "node_id": node_id,
                    "status": "completed",
                    "tool": tool_name,
                }

                # Store shaped executor output for subsequent steps and planner context.
                prior_results_map[node_id] = {"tool": tool_name, "output": shaped_for_exec}
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": json.dumps(shaped_for_exec, ensure_ascii=False),
                    "display": final_display,
                    "auto_paginated": False,
                    "cap_hit": False,
                    "node_id": node_id,
                })
            except Exception as e:
                logger.error(f"Tool execution failed at step {i+1}: {e}\n{traceback.format_exc()}")

                error_type = type(e).__name__
                if "HttpError" in error_type or "RuntimeError" in error_type:
                    msg = f"API Error executing `{tool_name}`: {e}"
                else:
                    msg = f"Internal bug executing `{tool_name}`: {e}"

                prior_results_map[node_id] = {"tool": tool_name, "output": {"error": msg}}
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": json.dumps({"error": msg}),
                    "display": f"**`{tool_name}`** — {msg}",
                    "auto_paginated": False,
                    "cap_hit": False,
                    "node_id": node_id,
                })

                # Only steps that actually need this one's output get skipped
                # — everything else in the plan still runs. Computed as a
                # transitive closure: a step depending on a skipped step is
                # itself skipped, and so on.
                newly_skipped = {node_id}
                growing = True
                while growing:
                    growing = False
                    for future_step in self.plan[i + 1:]:
                        fsid = future_step.get("step_id")
                        if fsid in newly_skipped:
                            continue
                        if any(d in newly_skipped for d in (future_step.get("depends_on") or [])):
                            newly_skipped.add(fsid)
                            growing = True
                skipped_step_ids.update(newly_skipped)

                n_downstream = len(newly_skipped) - 1
                downstream_note = (
                    f" {n_downstream} downstream step(s) that depend on it will be skipped."
                    if n_downstream else ""
                )
                progress_msg = f"\nStep {i+1} of {total_steps} failed.{downstream_note} Continuing with the rest of the plan...\n"
                yield {"text": f"{msg}\n{progress_msg}", "node_id": node_id, "status": "failed"}
                # Fall through to the next iteration of the step loop instead
                # of aborting — independent steps still get a chance to run.

        yield {"text": "\nExecution complete!", "node_id": None}

        # Append summary of results to chat history so the LLM remembers them for the next turn
        if tool_results:
            content = self._format_tool_results_markdown("Execution Results:", tool_results)
            await self._append_history("assistant", content, msg_type="tool_call")

            # Fix 3: Persist structured last tool results for the NEXT turn's Planner context block.
            # Store only the raw result string; the block builder will truncate to 400 chars.
            self._last_tool_results = [
                {"tool": r["tool"], "result": r["result"]}
                for r in tool_results
            ]
        else:
            self._last_tool_results = []

        # Go back to IDLE
        self.state = AgentState.IDLE
        self.plan = None

        # No LLM synthesis step — the step_result cards above already display the
        # tool output in a rich structured card. Skipping synthesis saves one full
