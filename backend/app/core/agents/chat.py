import json
import logging
import re
from typing import Dict, List, Optional, AsyncGenerator, Any
import anyio

from app.core.llm_manager import LLMManager
from app.core.feature_flags import CONNECTORS_ENABLED
from app.mcp.registry import mcp_registry
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

        # Pagination continuation state — persists across WAITING_LOOP_CONTINUATION await.
        # Cleared when the user says "stop" or when the cursor is exhausted.
        # Shape: {"step_index": int, "node_id": str, "tool_name": str, "cursor": str,
        #         "inject_arg": str, "accumulated": list, "prior_results_map": dict,
        #         "tool_results": list, "token_callback": callable|None}
        self._pagination_state: Dict[str, Any] = {}


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

    @staticmethod
    def _build_metadata_context() -> str:
        """
        Reads per-server account_context_json from the DB for all connected servers.
        Builds a human-readable block injected into the planner prompt so the LLM
        uses real authenticated values (e.g. GitHub username) instead of placeholders.
        Survives server restarts because the data lives in SQLite, not in memory.
        """
        try:
            from app.db.crud import get_all_server_account_contexts
            db = SessionLocal()
            all_ctx = get_all_server_account_contexts(db)
            db.close()
        except Exception:
            return ""
        if not all_ctx:
            return ""
        lines = ["\nCONNECTED ACCOUNT CONTEXT (use these real values when constructing arguments):"]
        for server, meta in all_ctx.items():
            for key, value in meta.items():
                lines.append(f"  {server} {key.replace('_', ' ')}: {value}")
        return "\n".join(lines)

    def _get_local_tools(self) -> List[Dict]:
        """
        Tools that aren't MCP servers. Two families:
          - export_document: pure-Python (app.core.exporter), needs no
            native install, so it's always offered — no Marketplace/
            capability gate.
          - Playwright-based web tooling (app.core.scraper /
            app.core.browser_session): only offered when it's both
            installed (via the Marketplace) and not toggled off for this
            specific conversation via the '+' menu's Tools switch — both
            share the one 'playwright_scraper' capability, since they're
            the same underlying Chromium install.
        """
        tools = [self._export_document_tool_def()]
        tools.extend(self._get_scraper_tools())
        return tools

    @staticmethod
    def _export_document_tool_def() -> Dict:
        return {
            "name": "export_document",
            "description": (
                "Converts markdown content into a downloadable PDF, DOCX, or XLSX "
                "file and returns a download link that appears directly in the chat "
                "for the user to click. Use when the user asks to export, download, "
                "save, or convert something — a prior response, a summary, a table — "
                "to one of these formats. Put the ACTUAL content to export in full in "
                "`content` (copy it from the relevant part of the conversation; don't "
                "just describe it) — this tool does not know what 'that' or 'it' "
                "refers to on its own. For anything row/column-shaped — especially "
                "when the target format is xlsx, or the user describes data with "
                "columns/fields — write it as a markdown table "
                "(`| col1 | col2 |` header, `|---|---|` separator, one data row per "
                "line), not a bulleted list or plain paragraph: only real markdown "
                "tables become an actual spreadsheet grid or a bordered table in the "
                "output, everything else becomes one line of plain text per item."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full markdown content to export (headings, lists, tables, etc. are all preserved).",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "xlsx"],
                        "description": "The file format to export to.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title used as the document heading and in the filename.",
                    },
                },
                "required": ["content", "format"],
            },
        }

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
        Single source of truth for "every tool that exists" — MCP-connected
        servers plus local tools (e.g. web_scrape). EVERY consumer that needs
        the full tool list (plan validation, execution, prompt building, the
        query-rewrite pass) MUST call this rather than reconstructing the
        union by hand.

        This exists because three separate call sites independently forgot
        `+ self._get_local_tools()` when web_scrape was added: plan
        validation rejected any plan selecting it as a "hallucinated tool",
        execute_plan hard-failed with "no MCP servers found" for anyone with
        zero MCP connectors, and the query-rewrite pass silently couldn't
        mention it in its own keyword expansion. Routing everything through
        one method makes that specific class of bug structurally impossible
        to repeat for the next local tool.
        """
        return mcp_registry.list_all_tools() + self._get_local_tools()

    def _search_available_tools(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Search-scoped variant of _all_available_tools: MCP tools ranked by
        relevance to `query` via FTS5, with local tools always included
        (there are few enough — currently one — that relevance-ranking them
        isn't worth the complexity, and the local-tools list is itself
        already gated by installed+active state).
        """
        return mcp_registry.search_tools(query, top_k=top_k) + self._get_local_tools()

    # Catalog keys/words too generic to reliably signal "the user meant this
    # specific connector" — common English words that show up in ordinary
    # requests having nothing to do with the connector of the same name.
    _GENERIC_CATALOG_KEYS = {"time", "memory", "git", "fetch", "filesystem", "sequential_thinking"}

    @staticmethod
    def _catalog_name_candidates(key: str, entry: dict) -> set:
        """Lowercased name variants worth matching for a catalog entry: its
        key, the key without a 'google_' prefix, and its display name with
        spaces stripped."""
        return {
            key.lower(),
            key.lower().replace("google_", ""),
            str(entry.get("display_name", "")).lower().replace(" ", ""),
        }

    def _suggest_connector_for_tool(self, tool_name: str) -> Optional[dict]:
        """
        Best-effort match for a hallucinated tool name against the connector
        catalog. Local models routinely hallucinate plausible-looking tool
        names for services they've seen in training data but were never
        actually given — since the planner prompt only ever lists tools from
        *connected* servers (see _all_available_tools), it has no way to know
        e.g. Notion's real tool names, so it guesses something like
        'notion_search_pages'. Matching that guess back to the "notion" catalog
        entry lets the failure message say "connect Notion" instead of the
        much less useful "hallucinated invalid tool".
        """
        if not CONNECTORS_ENABLED:
            return None
        from app.mcp.catalog import CONNECTORS_CATALOG

        name_lower = (tool_name or "").lower()
        if not name_lower:
            return None

        connected = {
            server.lower() for server, info in mcp_registry.get_status().items()
            if info.get("running")
        }

        for key, entry in CONNECTORS_CATALOG.items():
            if key.lower() in connected:
                continue  # already connected — not the gap we're explaining
            candidates = self._catalog_name_candidates(key, entry)
            if any(c and len(c) > 2 and c in name_lower for c in candidates):
                return entry
        return None

    def _mentioned_catalog_services(self, user_message: str) -> Dict[str, dict]:
        """
        Scans the user's own message (never the model's output — see callers)
        for specific, recognizable connector names from the catalog, e.g.
        "notion" or "slack". Deliberately conservative: skips generic-English
        catalog keys (see _GENERIC_CATALOG_KEYS) and requires a whole-word
        match on a name longer than 3 characters, so ordinary loosely-worded
        requests don't produce false hits. Returns {catalog_key: entry} for
        every match, regardless of connection status — callers decide what
        connection state means for them.
        """
        from app.mcp.catalog import CONNECTORS_CATALOG
        import re

        msg_lower = user_message.lower()
        mentioned: Dict[str, dict] = {}
        for key, entry in CONNECTORS_CATALOG.items():
            if key in self._GENERIC_CATALOG_KEYS:
                continue
            for candidate in self._catalog_name_candidates(key, entry):
                if candidate and len(candidate) > 3 and re.search(rf"\b{re.escape(candidate)}\b", msg_lower):
                    mentioned[key] = entry
                    break
        return mentioned

    def _find_missing_connector_for_request(self, user_message: str) -> Optional[dict]:
        """
        Pre-flight check — run BEFORE any plan is generated, so a request for
        a service that's obviously not connected never burns a query-rewrite
        LLM pass *and* a planner LLM pass (each a real cost on a local model)
        just to fail validation afterward. If the user's own message names a
        specific, real connector that isn't currently connected, there's
        nothing a plan could accomplish — tell them directly.

        Same conservative matching as _mentioned_catalog_services — only
        fires on an explicit, unambiguous connector name.
        """
        if not CONNECTORS_ENABLED:
            return None
        connected = {
            server.lower() for server, info in mcp_registry.get_status().items()
            if info.get("running")
        }
        for key, entry in self._mentioned_catalog_services(user_message).items():
            if key.lower() not in connected:
                return entry
        return None

    def _check_cross_service_mismatch(self, user_message: str, plan: List[Dict]) -> List[str]:
        """
        Catches the *other* half of tool hallucination: the planner picking a
        real, connected tool that's simply the wrong one — e.g. reaching for
        a GitHub tool when the user explicitly asked about Notion, even
        though Notion is connected. Grammar constraints (see planner.py) make
        an invented tool name structurally impossible, but they can't stop
        the model from validly using the wrong real tool; that's a relevance
        mistake, not a vocabulary one.

        Doesn't overlap with _find_missing_connector_for_request: that
        pre-flight check already blocks any request naming a service that
        ISN'T connected, before a plan is even generated — so by the time
        this runs, every mentioned service here is necessarily connected.
        This check exists purely for "named, connected, but the wrong one
        got used anyway".

        Deliberately conservative to avoid false positives on ordinary
        loosely-worded requests: only fires when the user's own message names
        a specific, recognizable connector (e.g. "notion", "slack") that is
        NOT the connector the chosen tool actually belongs to. It checks the
        user's original words, never the model's own step "reason" text —
        that's generated by the same model that might be making the mistake,
        so it isn't independent evidence.
        """
        from app.mcp.catalog import CONNECTORS_CATALOG

        mentioned = self._mentioned_catalog_services(user_message)
        if not mentioned:
            return []

        warnings = []
        for step in plan:
            tool_name = step.get("tool")
            owning_server = mcp_registry.get_server_for_tool(tool_name)
            if not owning_server:
                continue  # local tool (e.g. web_scrape) — no catalog service to compare against
            if owning_server not in mentioned:
                # The user named a specific service, and it isn't this one.
                named = ", ".join(e["display_name"] for e in mentioned.values())
                owning_entry = CONNECTORS_CATALOG.get(owning_server, {})
                owning_display = owning_entry.get("display_name", owning_server)
                warnings.append(
                    f"You mentioned {named}, but step using `{tool_name}` is a "
                    f"{owning_display} tool — double-check this is actually the right one."
                )
        return warnings

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
        / browser_extract_text result, telling the model how to read further
        into a page that didn't fit in one chunk, and/or that the page needs
        login and can't be accessed (public sites only — see
        app.core.scraper's module docstring). Only these three tool names
        ever set has_more/needs_auth on their outcome — every other
        browser_* action never calls this.
        """
        parts = []
        if outcome.get("has_more"):
            call_hint = (
                f"Call web_scrape again with the same url and offset={outcome['next_offset']}"
                if tool_name == "web_scrape"
                else f"Call browser_extract_text with offset={outcome['next_offset']}"
            )
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
        Dispatch for the export_document local tool. Converts markdown to a
        file (app.core.exporter.export_markdown — the exact same converter
        the manual "+" Export menu uses) and stores it under a short-lived
        ID (app.api.export.store_export) instead of returning the bytes
        themselves — this runs inside the agent's tool-calling loop, not an
        HTTP handler, so there's no request/response cycle to hand raw
        file bytes back through. The returned download_url is what actually
        lets the user get the file: an absolute link to this backend's own
        /api/export/download/{id} route, rendered as a normal markdown link
        in the chat message.
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
        """Fetches all available tools from all connected MCP servers, plus local tools like web_scrape."""
        tools = self._all_available_tools()
        if not tools:
            return "No active MCP servers connected. Please authenticate with Google or connect a server first."
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in tools)
        return tools_str + self._build_metadata_context()

    def _rewrite_query_for_search(self, query: str) -> tuple[str, bool]:
        """Uses a fast LLM pass to expand the user's query with keywords likely to hit the FTS5 tool index. Also flags if query is counting."""
        llm = self.get_llm()
        if not llm:
            return query, False
            
        all_tools = self._all_available_tools()
        if not all_tools:
            return query, False

        tool_names = ", ".join([t["name"] for t in all_tools])
        
        prompt = f"""You are a fast tool selector for an AI agent.
The user's query is: "{query}"

Available tools in the registry: [{tool_names}]

Analyze the user's query and output a JSON object with two keys:
- "tools": either the exact string "ALL_TOOLS" (if they ask a general question about what tools are available), OR a list of the 1 to 5 most relevant tool names from the registry.
- "is_counting": boolean true if the user query implies needing a total, count, or completeness (e.g. "how many", "count of", "all of", "list all"). Otherwise false.

Do NOT invent new tool names. Output valid JSON only.
Example: {{"tools": ["slack_send_message", "google_drive_find_file"], "is_counting": false}}"""

        try:
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=60,
                response_format={"type": "json_object"}
            )
            content = response["choices"][0]["message"]["content"].strip()
            import json
            data = json.loads(content)
            tools_val = data.get("tools", [])
            is_counting = bool(data.get("is_counting", False))
            
            if tools_val == "ALL_TOOLS" or (isinstance(tools_val, list) and "ALL_TOOLS" in tools_val):
                expanded_keywords = "ALL_TOOLS"
            elif isinstance(tools_val, list):
                expanded_keywords = ", ".join(tools_val)
            else:
                expanded_keywords = str(tools_val)
                
            logger.info(f"Query rewritten for tool search: '{query}' -> '{expanded_keywords}' (counting: {is_counting})")
            return expanded_keywords, is_counting
        except Exception as e:
            logger.error(f"Query rewrite failed: {e}")
            return query, False

    def get_searched_tools(self, query: str) -> tuple[str, bool, List[str]]:
        """
        Fetches top-k relevant tools from registry using keyword expansion and
        SQLite FTS5. Also returns the plain list of tool names shown — this is
        the exact set the planner's grammar gets constrained to, so the model
        is structurally unable to name a tool it wasn't actually offered.
        """
        optimized_query, is_counting = self._rewrite_query_for_search(query)

        if "ALL_TOOLS" in optimized_query:
            all_tools = self._all_available_tools()
            tools_str = "\n".join(self._format_tool_for_planner(t) for t in all_tools)
            if not tools_str:
                return "", False, []
            return tools_str + self._build_metadata_context(), is_counting, [t["name"] for t in all_tools]

        tools = self._search_available_tools(optimized_query, top_k=10)
        if not tools:
            return "", False, []
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in tools)
        return tools_str + self._build_metadata_context(), is_counting, [t["name"] for t in tools]

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
        try:
            response = llm.create_chat_completion(
                messages=messages,
                temperature=0.7,
                stream=True
            )
            full_response = ""
            for chunk in response:
                if getattr(self, "cancel_event", None) and self.cancel_event.is_set():
                    logger.info("Text generation cancelled.")
                    break
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        token = delta["content"]
                        full_response += token
                        if token_callback:
                            token_callback(token)
            self._log_token_usage(llm, messages, full_response, "chat", self.connection_id)
            return full_response
        except Exception as e:
            logger.error(f"LLM TEXT call failed: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # State machine
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_message(self, message: str, mode: str = "chat", token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None) -> str:
        """Main state machine dispatcher."""

        if message == "__system_mode_switch__":
            if self.state in [
                AgentState.WAITING_CONFIRMATION, AgentState.WAITING_LOOP_CONTINUATION,
            ]:
                self.state = AgentState.IDLE
                self.plan = None
                self._pagination_state = {}
                return "__system_toast__:Pending action discarded."
            return ""

        if self.state == AgentState.IDLE:
            return await self._handle_idle(message, mode, token_callback, status_callback, attachments)

        elif self.state == AgentState.WAITING_CONFIRMATION:
            return await self._handle_confirmation(message, token_callback)

        elif self.state == AgentState.EXECUTING:
            return "I am currently executing the tasks. Please wait..."

        elif self.state == AgentState.WAITING_LOOP_CONTINUATION:
            return await self._handle_loop_continuation(message)

        return "Unknown state."

    async def _handle_idle(self, message: str, mode: str = "chat", token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None) -> str:
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
        _MAX_RAG_CHUNKS       = _chat_cfg.get("max_rag_chunks", 5)

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
            # Chat Mode does no tool calling at all — that's Agent Mode's job
            # exclusively now (a deliberate product decision: one clear place
            # tools execute, with the plan/confirm/execute ceremony and the
            # grounding checks that come with it, rather than two divergent
            # tool paths of different rigor). A URL is still detected — same
            # cheap deterministic regex check as before — but instead of
            # fetching it, Chat Mode nudges the user to Agent Mode rather
            # than silently ignoring an obvious intent.
            url = self._extract_url(message)
            if url:
                nudge = (
                    f"I can't open web pages in Chat Mode — switch to **Agent Mode** "
                    f"(the toggle below) and ask me again to have me look at {url}."
                )
                await self._append_history("assistant", nudge)
                return nudge

            if status_callback:
                await status_callback("Searching your documents...")

            # 1. RAG Retrieval for Uploaded Documents
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                from app.core.rag.processor import hybrid_search
                relevant_chunks = await loop.run_in_executor(
                    db_executor,
                    lambda: hybrid_search(query=message, conversation_id=self.connection_id, top_k=_MAX_RAG_CHUNKS)
                )
            except Exception as e:
                logger.warning(f"RAG search failed: {e}")
                relevant_chunks = []
                
            if status_callback:
                await status_callback("Generating...")
                
            document_context = ""
            if relevant_chunks:
                logger.info(f"RAG retrieved {len(relevant_chunks)} chunks for query: {message}")
                document_context = "Relevant excerpts from your uploaded documents:\n\n"
                for chunk in relevant_chunks:
                    document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"
            else:
                logger.info(f"RAG retrieved 0 chunks for query: {message}")

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

            from app.prompts.chat import build_chat_prompt
            # Append document context to the base entity context
            full_context = entity_context
            if document_context:
                full_context += "\n" + document_context
            if skills_metadata:
                full_context += "\n\n" + skills_metadata
            if triggered_skills_block:
                full_context += "\n\n" + triggered_skills_block

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

            # In chat mode, we expect pure raw text, no JSON.
            import asyncio
            loop = asyncio.get_running_loop()
            chat_response = await loop.run_in_executor(
                llm_executor,
                lambda: self._call_llm_text(messages, token_callback)
            )
            
            await self._append_history("assistant", chat_response)

            return chat_response

        # If mode == "agent", we skip the Chat LLM and go straight to Plan Generation.
        # First, a zero-cost pre-flight check: if the request names a specific
        # connector that plainly isn't connected, there's no plan worth
        # generating — skip straight to telling the user, before spending a
        # query-rewrite LLM pass *and* a full planner LLM pass on something
        # already known to fail. (Doesn't try to catch "no tool fits at all"
        # in general — only this precise, cheap, high-confidence case.)
        missing_connector = self._find_missing_connector_for_request(message)
        if missing_connector:
            response = (
                f"I'd need **{missing_connector['display_name']}** connected to do that — "
                "head to **Connectors** to add it, then ask me again."
            )
            await self._append_history("assistant", response)
            return response

        import asyncio
        loop = asyncio.get_running_loop()
        tools_str, is_counting, offered_tool_names = await loop.run_in_executor(llm_executor, self.get_searched_tools, message)
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
        plan_json_str = await loop.run_in_executor(
            llm_executor,
            lambda: self.planner.generate_plan(
                message, tools_str, entity_context, history_for_planner,
                token_callback=None, is_counting=is_counting, tool_names=offered_tool_names,
            )
        )

        try:
            plan_data = json.loads(plan_json_str, strict=False)
        except json.JSONDecodeError:
            self.state = AgentState.IDLE
            self.plan = None
            return "Planner generated invalid JSON. Please try your request again."
        
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
            question = plan_data.get("clarifying_question")
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
            suggestion = self._suggest_connector_for_tool(bad_tool_name)
            if suggestion:
                return (
                    f"I tried to use a **{suggestion['display_name']}** tool (`{bad_tool_name}`), "
                    f"but {suggestion['display_name']} isn't connected — head to **Connectors** "
                    "to add it, then ask me again."
                )
            return (
                f"I don't have a tool for that (`{bad_tool_name}`) — either the connector it "
                "needs isn't set up, or it's not something I can do yet. Check **Connectors** or "
                "**Marketplace** for what's available, or try rephrasing your request."
            )
        
        # 2. Metadata Validation (Dependencies, IDs)
        self.plan = []
        
        if isinstance(plan_data, dict):
            warnings = plan_data.get("warnings", [])
        else:
            warnings = []
            
        if isinstance(warnings, str):
            warnings = [warnings]

        # Catches a real, connected tool being the *wrong* one for what the
        # user actually asked for (grammar constraints only stop invented
        # tool names — see planner.py — not a valid tool used for the wrong
        # service). Never overlaps with the pre-flight connector check: that
        # one already blocks a request naming a disconnected service before
        # a plan exists at all.
        warnings.extend(self._check_cross_service_mismatch(message, raw_plan))

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
                response = direct_response
            elif warnings:
                response = "**Note:**\n" + "\n".join([f"- {w}" for w in warnings])
            else:
                # The model returned neither a plan nor a direct_response — a
                # degenerate output the planner prompt explicitly tells it not
                # to produce (see the capability-question rule), but a small
                # local model can still miss it occasionally. Rather than
                # dumping the full tool list with every argument and
                # description, name just the tools themselves and ask what to
                # do — short and readable instead of overwhelming.
                tool_names = sorted({t["name"] for t in self._all_available_tools()})
                names_str = ", ".join(f"`{n}`" for n in tool_names) if tool_names else "no tools"
                response = f"I have access to: {names_str}. What would you like me to do?"

            await self._append_history("assistant", response)
                
            return response

        self.state = AgentState.WAITING_CONFIRMATION

        from app.mcp.pagination_registry import is_write_tool

        response = "**Proposed Execution Plan:**\n\n"
        for i, step in enumerate(self.plan):
            tool_name = step.get("tool")
            scope = step.get("fetch_scope", "single")
            # Tells the user, at a glance, which steps only look at data and
            # which ones actually change something, before they hand out one
            # blanket "yes" for the whole plan.
            is_write = is_write_tool(tool_name, tool_schemas.get(tool_name, {}))
            action_badge = "writes" if is_write else "read-only"
            response += f"**Step {i+1}: `{tool_name}`** `[{action_badge}]` `[scope: {scope}]`\n"
            if step.get("reason"):
                response += f"> {step.get('reason')}\n"

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
                # Fallback for entries that never went through the pagination
                # loop (e.g. a cap-hit continuation) — best-effort readable text.
                try:
                    parsed = json.loads(r["result"])
                    display = json.dumps(parsed, indent=2)[:800]
                except (json.JSONDecodeError, TypeError):
                    display = str(r.get("result", ""))[:800]
            parts.append(f"\n**`{tool_name}`**\n{display}")
        return "\n".join(parts)

    async def _handle_confirmation(self, message: str, token_callback=None) -> str:
        """WAITING_CONFIRMATION → confirm → EXECUTING  or  refine plan."""
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
                raw_refined = plan_data.get("plan", [])

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
                    response += f"{i+1}. **{step.get('tool')}**: {step.get('reason')}\n"
                response += "\nIs this better? (Reply 'yes' to proceed)"
                
                await self._append_history("assistant", response)
                    
                return response
            except json.JSONDecodeError:
                return "Error parsing refined plan from LLM."

    async def _handle_loop_continuation(self, message: str) -> str:
        """WAITING_LOOP_CONTINUATION → user says keep going or stop."""
        await self._append_history("user", message)

        positive_keywords = ["yes", "continue", "keep going", "more", "go ahead", "proceed"]
        is_continue = self._matches_any_keyword(message, positive_keywords)

        ps = self._pagination_state
        if not ps:
            self.state = AgentState.IDLE
            return "No pagination state found. What would you like to do next?"

        tool_results: list = ps.get("tool_results", [])

        if not is_continue:
            # User said stop — finalize with whatever was collected, the same
            # way a normal execute_plan completion does (no separate LLM
            # synthesis pass — the step_result cards already streamed live
            # during the original run show the data directly).
            self._pagination_state = {}
            self.state = AgentState.IDLE
            self.plan = None

            if tool_results:
                content = self._format_tool_results_markdown("Execution Results:", tool_results)
                await self._append_history("assistant", content, msg_type="tool_call")
                self._last_tool_results = [{"tool": r["tool"], "result": r["result"]} for r in tool_results]

            response = "Got it — proceeding with the data collected so far. Execution complete!"
            await self._append_history("assistant", response)
            return response

        # User said continue — hand off to _continue_pagination via
        # execute_plan (see its delegation at the top), which resumes the
        # capped/stalled step from current_arguments (already advanced to
        # the next page/cursor) rather than restarting the plan.
        self.state = AgentState.EXECUTING
        response = f"Continuing to fetch more pages for `{ps.get('tool_name', 'the tool')}`..."
        await self._append_history("assistant", response)
        return response

    async def _continue_pagination(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Resumes exactly one step that previously hit its pagination cap or a
        cursor stall (see WAITING_LOOP_CONTINUATION), picking up from
        `current_arguments` — already advanced to the next page/cursor at
        the moment it paused — instead of restarting the whole plan from
        step 1. Only this one step is touched: every other already-completed
        entry in tool_results/prior_results_map is carried through untouched,
        so an earlier write-capable step (e.g. create_issue) never fires
        twice just because a later read step needed more pages.

        Not a full re-run of the plan-execution machinery — no Executor LLM
        call, no schema/grounding checks — because none of that applies to
        continuing a tool call that already passed them once.
        """
        ps = self._pagination_state
        self._pagination_state = {}
        if not ps:
            self.state = AgentState.IDLE
            yield {"text": "No pagination state to resume — nothing to continue.", "node_id": None}
            return

        from app.mcp.pagination_registry import get_next_cursor, is_tool_safe_to_autoloop
        from app.mcp.response_shapers import shape_for_executor, shape_accumulated_response

        tool_results: List[Dict] = ps["tool_results"]
        prior_results_map: Dict[str, Any] = ps["prior_results_map"]
        tool_name = ps["tool_name"]
        node_id = ps["node_id"]
        current_arguments = dict(ps["current_arguments"])
        accumulated_items: List[Any] = list(ps["accumulated_items"])
        accumulated_raw: List[Any] = list(ps["accumulated_raw"])
        auto_paginated = ps.get("auto_paginated", True)
        fetch_scope = ps.get("fetch_scope", "exhaustive")
        schema = ps.get("schema", {})
        prev_cursor_value = ps.get("prev_cursor_value")

        _PAGE_CAP = 20
        _SAMPLE_CAP = 3
        page_cap = _PAGE_CAP if fetch_scope == "exhaustive" else _SAMPLE_CAP
        safe_to_loop = is_tool_safe_to_autoloop(tool_name, schema)

        cap_hit = False
        loop_stop_reason = "cap"

        yield {"text": f"\nContinuing `{tool_name}`…\n", "node_id": node_id, "status": "running"}

        for page_num in range(page_cap):
            if page_num > 0:
                yield {"text": f"  ↳ Page {page_num + 1}…\n", "node_id": node_id, "status": "running"}
                auto_paginated = True

            result = await anyio.to_thread.run_sync(
                lambda t=tool_name, a=dict(current_arguments): mcp_registry.call_tool(t, a)
            )
            try:
                raw_parsed = json.loads(str(result)) if isinstance(result, str) else result
            except (json.JSONDecodeError, TypeError):
                raw_parsed = result

            accumulated_items.append(shape_for_executor(tool_name, raw_parsed))
            accumulated_raw.append(raw_parsed)

            if not safe_to_loop:
                loop_stop_reason = "safety"
                break

            if isinstance(raw_parsed, list):
                per_page_default = int(current_arguments.get("per_page", 30))
                if len(raw_parsed) >= per_page_default:
                    current_arguments["page"] = current_arguments.get("page", 1) + 1
                    continue
                loop_stop_reason = "last_page"
                break

            cursor_info = get_next_cursor(tool_name, raw_parsed if isinstance(raw_parsed, dict) else {})
            if cursor_info is None:
                loop_stop_reason = "last_page"
                break
            new_cursor = cursor_info["cursor_value"]
            if new_cursor == prev_cursor_value:
                loop_stop_reason = "stall"
                break
            prev_cursor_value = new_cursor
            current_arguments[cursor_info["inject_arg"]] = new_cursor
        else:
            cap_hit = True
            loop_stop_reason = "cap"

        final_display = shape_accumulated_response(
            tool_name, accumulated_items, len(accumulated_items), raw_items=accumulated_raw
        )
        yield {"type": "step_result", "text": final_display, "node_id": node_id, "status": "completed", "tool": tool_name}

        final_exec_output = (
            accumulated_items[0] if len(accumulated_items) == 1
            else {
                "pages": accumulated_items,
                "total_pages_fetched": len(accumulated_items),
                "auto_paginated": auto_paginated,
                "cap_hit": cap_hit,
            }
        )

        # Patch this one step's entry in place — find it by node_id so a
        # duplicate tool name elsewhere in the plan is never mismatched.
        prior_results_map[node_id] = {"tool": tool_name, "output": final_exec_output}
        patched_entry = {
            "tool": tool_name,
            "arguments": current_arguments,
            "result": json.dumps(final_exec_output, ensure_ascii=False),
            "display": final_display,
            "auto_paginated": auto_paginated,
            "cap_hit": cap_hit,
            "node_id": node_id,
        }
        for idx, r in enumerate(tool_results):
            if r.get("node_id") == node_id:
                tool_results[idx] = patched_entry
                break
        else:
            tool_results.append(patched_entry)

        if loop_stop_reason == "stall":
            self._pagination_state = {
                "tool_results": tool_results,
                "prior_results_map": prior_results_map,
                "tool_name": tool_name,
                "node_id": node_id,
                "current_arguments": current_arguments,
                "accumulated_items": accumulated_items,
                "accumulated_raw": accumulated_raw,
                "auto_paginated": auto_paginated,
                "fetch_scope": fetch_scope,
                "schema": schema,
                "prev_cursor_value": prev_cursor_value,
            }
            self.state = AgentState.WAITING_LOOP_CONTINUATION
            yield {
                "text": (
                    f"\n\n**Pagination Stalled Again** for `{tool_name}`\n\n"
                    f"Still stuck after {len(accumulated_items)} page(s) — this API's cursor "
                    "genuinely isn't advancing. Reply **'yes'** to try once more or **'no'** to "
                    "proceed with what was collected."
                ),
                "node_id": None,
                "status": "waiting",
            }
            return

        if cap_hit:
            self._pagination_state = {
                "tool_results": tool_results,
                "prior_results_map": prior_results_map,
                "tool_name": tool_name,
                "node_id": node_id,
                "current_arguments": current_arguments,
                "accumulated_items": accumulated_items,
                "accumulated_raw": accumulated_raw,
                "auto_paginated": auto_paginated,
                "fetch_scope": fetch_scope,
                "schema": schema,
                "prev_cursor_value": prev_cursor_value,
            }
            self.state = AgentState.WAITING_LOOP_CONTINUATION
            yield {
                "text": (
                    f"\n\n**Pagination cap reached again** for `{tool_name}`. "
                    f"{len(accumulated_items)} pages fetched so far.\n\n"
                    "**Continue fetching more pages?** Reply **'yes'** to fetch another batch "
                    "or **'no'** to proceed with what I have."
                ),
                "node_id": None,
                "status": "waiting",
            }
            return

        yield {"text": "\nExecution complete!", "node_id": None}

        content = self._format_tool_results_markdown("Execution Results:", tool_results)
        await self._append_history("assistant", content, msg_type="tool_call")
        self._last_tool_results = [{"tool": r["tool"], "result": r["result"]} for r in tool_results]

        self.state = AgentState.IDLE
        self.plan = None

    # ─────────────────────────────────────────────────────────────────────────
    # Plan execution
    # ─────────────────────────────────────────────────────────────────────────

    async def execute_plan(self, token_callback=None) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes the approved plan step by step, then proposes entities to remember."""
        if self.state != AgentState.EXECUTING or not self.plan:
            yield {"text": "No plan to execute.", "node_id": None}
            return

        # Resuming after WAITING_LOOP_CONTINUATION ("continue fetching more
        # pages?") is a completely different operation from running the plan
        # — every step already ran once; only the capped/stalled step needs
        # revisiting. Delegate entirely rather than falling into the normal
        # step loop below, which would silently re-run every step from
        # scratch (including write-capable ones a second time).
        if self._pagination_state:
            async for progress in self._continue_pagination():
                yield progress
            return

        all_tools = self._all_available_tools()
        if not all_tools:
            yield {"text": "Error: No connected MCP servers or active local tools found.", "node_id": None}
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

        from app.mcp.pagination_registry import get_next_cursor, is_tool_safe_to_autoloop

        # Pagination constants
        _PAGE_CAP = 20         # max pages per step for exhaustive scope
        _SAMPLE_CAP = 3        # max pages for sample scope

        # Run each tool step and collect raw results
        tool_results: List[Dict] = []
        # Stores structured output per step_id for the Executor — avoids prose-parsing for IDs.
        # Format: {node_id: {"tool": tool_name, "output": <parsed JSON or raw string>}}
        prior_results_map: Dict[str, Any] = {}
        total_steps = len(self.plan)
        # Captures exactly what a cap-hit step needs to genuinely continue
        # from (next page/cursor, pages gathered so far) — see _continue_pagination.
        step_resume_snapshots: Dict[str, Dict] = {}
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

            # Generate arguments live using the deterministic Executor Agent
            import asyncio
            loop = asyncio.get_running_loop()
            arguments = await loop.run_in_executor(
                llm_executor,
                lambda: self.executor.generate_arguments(
                    tool_name=tool_name,
                    tool_schema=schema,
                    overall_plan=self.plan,
                    step_reason=step_reason,
                    prior_results=prior_results_for_executor,
                    entity_context=entity_context,
                    user_request=full_chat_history
                )
            )

            # Handle Executor Escape Hatch
            if isinstance(arguments, dict) and "error" in arguments:
                err_msg = arguments["error"]
                logger.error(f"Executor aborted for {tool_name}: {err_msg}")
                yield {"text": f"Plan aborted: {err_msg}\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

            # Clean up known LLM hallucinations before validation
            if isinstance(arguments, dict):
                # Small models often bleed the 'fetch_scope' step parameter into the arguments dict
                if "fetch_scope" in arguments and "fetch_scope" not in schema.get("properties", {}):
                    del arguments["fetch_scope"]

            # Single strict check to catch catastrophic failure
            try:
                jsonschema.validate(instance=arguments, schema=schema)
            except jsonschema.exceptions.ValidationError as e:
                logger.error(f"Executor failed schema validation for {tool_name}: {e.message}")
                yield {"text": f"Plan aborted: Executor generated invalid arguments for `{tool_name}`: {e.message}\n", "node_id": node_id, "status": "failed"}
                self.state = AgentState.IDLE
                self.plan = None
                return

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
                from app.mcp.response_shapers import shape_for_executor, shape_for_display

                # ── Pagination-aware execution loop ────────────────────────────
                fetch_scope = step.get("fetch_scope", "single")

                is_local_web_tool = (
                    tool_name == "web_scrape"
                    or tool_name.startswith("browser_")
                    or tool_name == "export_document"
                )

                # Determine page cap and safety based on scope
                if is_local_web_tool:
                    # A single action against one page/session/export, never
                    # paginated the MCP way — ignore whatever fetch_scope
                    # the planner assigned. (browser_extract_text has its
                    # own, different pagination — see offset/next_offset.)
                    page_cap = 1
                    safe_to_loop = True
                elif fetch_scope == "exhaustive":
                    page_cap = _PAGE_CAP
                    safe_to_loop = is_tool_safe_to_autoloop(tool_name, schema)
                elif fetch_scope == "sample":
                    page_cap = _SAMPLE_CAP
                    safe_to_loop = is_tool_safe_to_autoloop(tool_name, schema)
                else:  # "single" or unrecognized
                    page_cap = 1
                    safe_to_loop = True  # single page — gate irrelevant

                accumulated_items: List[Any] = []
                # Parallel list of true, unshaped tool output — kept only so the
                # single-page display fallback can show the real thing instead of
                # the executor-shaped (renamed/pruned) version. See
                # shape_accumulated_response's single-page fallback.
                accumulated_raw: List[Any] = []
                current_arguments = dict(arguments)
                # Inject page=1 for paginated tools if not already present
                if "page" not in current_arguments and fetch_scope in ("exhaustive", "sample"):
                    current_arguments["page"] = 1
                prev_cursor_value = None
                cap_hit = False
                auto_paginated = False
                loop_stop_reason = "single"  # single | last_page | safety | stall | cap

                for page_num in range(page_cap):
                    if page_num == 0:
                        verb = "Fetching" if page_cap == 1 else "Fetching all pages of"
                        yield {"text": f"{verb} `{tool_name}`…\n", "node_id": node_id, "status": "running"}
                    else:
                        yield {"text": f"  ↳ Page {page_num + 1}…\n", "node_id": node_id, "status": "running"}
                        auto_paginated = True

                    if is_local_web_tool:
                        # Not an MCP server — routed through the same shared,
                        # deliberately ephemeral dispatch Chat Mode's own
                        # tool-calling uses (_execute_web_scrape /
                        # _execute_browser_action): no persistence, no
                        # Qdrant embedding, nothing saved beyond this turn's
                        # result. If a later question needs this content
                        # again, the model must call the tool again — for
                        # web_scrape that means a fresh fetch; for the
                        # browser_* tools, the session (and whatever page
                        # is loaded) is still there to read further.
                        if tool_name == "web_scrape":
                            outcome = await self._execute_web_scrape(current_arguments)
                        elif tool_name == "export_document":
                            outcome = await self._execute_export_document(current_arguments)
                        else:
                            outcome = await self._execute_browser_action(tool_name, current_arguments)

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
                                # web_scrape / browser_navigate / browser_extract_text
                                # — all chunked identically via _chunk_text, capped
                                # there (not here), so has_more/next_offset stay in
                                # sync with what text_preview actually holds.
                                result = {
                                    "title": outcome.get("title"),
                                    "text_preview": outcome["text"],
                                    "warnings": outcome.get("warnings"),
                                }
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
                    else:
                        result = await anyio.to_thread.run_sync(
                            lambda t=tool_name, a=dict(current_arguments): mcp_registry.call_tool(t, a)
                        )

                    # Parse raw result
                    try:
                        raw_parsed = json.loads(str(result)) if isinstance(result, str) else result
                    except (json.JSONDecodeError, TypeError):
                        raw_parsed = result

                    shaped_for_exec = shape_for_executor(tool_name, raw_parsed)
                    accumulated_items.append(shaped_for_exec)
                    accumulated_raw.append(raw_parsed)
                    # ↑ Do NOT yield a step_result here. Hold everything until all
                    #   pages are done, then emit one smart summary card below.

                    # ── Decide next action ─────────────────────────────────────
                    if page_cap == 1:
                        loop_stop_reason = "single"
                        break

                    if not safe_to_loop:
                        loop_stop_reason = "safety"
                        break

                    # GitHub-style: plain list → infer more pages from count
                    if isinstance(raw_parsed, list):
                        per_page_default = int(current_arguments.get("per_page", 30))
                        if len(raw_parsed) >= per_page_default:
                            current_arguments["page"] = current_arguments.get("page", 1) + 1
                            logger.info(
                                f"[Pagination] Full page ({len(raw_parsed)} items) — fetching page {current_arguments['page']}"
                            )
                            continue
                        else:
                            loop_stop_reason = "last_page"
                            logger.info(
                                f"[Pagination] Partial page ({len(raw_parsed)}/{per_page_default}) — done"
                            )
                            break

                    # Cursor-based tools (Gmail, Notion, Drive, Slack, …)
                    cursor_info = get_next_cursor(
                        tool_name, raw_parsed if isinstance(raw_parsed, dict) else {}
                    )
                    if cursor_info is None:
                        loop_stop_reason = "last_page"
                        break

                    new_cursor = cursor_info["cursor_value"]
                    if new_cursor == prev_cursor_value:
                        loop_stop_reason = "stall"
                        logger.warning(f"[Pagination] Cursor stall for '{tool_name}'")
                        break

                    prev_cursor_value = new_cursor
                    current_arguments = dict(arguments)
                    current_arguments[cursor_info["inject_arg"]] = new_cursor
                else:
                    cap_hit = True
                    loop_stop_reason = "cap"

                # ── Emit ONE final result card ─────────────────────────────────
                from app.mcp.response_shapers import shape_accumulated_response
                final_display = shape_accumulated_response(
                    tool_name, accumulated_items, len(accumulated_items), raw_items=accumulated_raw
                )
                yield {
                    "type": "step_result",
                    "text": final_display,
                    "node_id": node_id,
                    "status": "completed",
                    "tool": tool_name,
                }

                # ── Warn user for non-goal-reaching stops ─────────────────────
                if loop_stop_reason == "safety":
                    yield {
                        "text": (
                            f"\n> **Note:** `{tool_name}` is a write-capable or unreviewed tool "
                            "and cannot be auto-paginated for safety. Only the first page was fetched. "
                            "Ask me to fetch the next page explicitly if you need more.\n"
                        ),
                        "node_id": node_id,
                        "status": "completed",
                    }
                elif loop_stop_reason == "stall":
                    # Cursor didn't advance — treat like a cap hit and ask user.
                    # "Retry" here just means trying the same (stalled) cursor
                    # once more — a real API hiccup is the only thing that
                    # would make that succeed; a genuine stall will stall
                    # again, which _continue_pagination handles the same way.
                    self._pagination_state = {
                        "tool_results": tool_results,
                        "prior_results_map": prior_results_map,
                        "tool_name": tool_name,
                        "node_id": node_id,
                        "current_arguments": current_arguments,
                        "accumulated_items": accumulated_items,
                        "accumulated_raw": accumulated_raw,
                        "auto_paginated": auto_paginated,
                        "fetch_scope": fetch_scope,
                        "schema": schema,
                        "prev_cursor_value": prev_cursor_value,
                    }
                    self.state = AgentState.WAITING_LOOP_CONTINUATION
                    yield {
                        "text": (
                            f"\n\n**Pagination Stalled** for `{tool_name}`\n\n"
                            f"The cursor stopped advancing after {len(accumulated_items)} page(s). "
                            "This may mean the API returned the same page twice, or all results have been collected.\n\n"
                            "__PAGINATION_CAP__\n\n"
                            "**Would you like to stop here or retry?** Reply **'yes'** to retry from the next page or **'no'** to proceed with what was collected."
                        ),
                        "node_id": None,
                        "status": "waiting",
                    }
                    return

                # Build the merged exec output for subsequent plan steps and history
                final_exec_output = (
                    accumulated_items[0] if len(accumulated_items) == 1
                    else {
                        "pages": accumulated_items,
                        "total_pages_fetched": len(accumulated_items),
                        "auto_paginated": auto_paginated,
                        "cap_hit": cap_hit,
                    }
                )

                # Store shaped executor output for subsequent steps and planner context.
                prior_results_map[node_id] = {"tool": tool_name, "output": final_exec_output}
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": json.dumps(final_exec_output, ensure_ascii=False),
                    "display": final_display,
                    "auto_paginated": auto_paginated,
                    "cap_hit": cap_hit,
                    "node_id": node_id,
                })

                if cap_hit:
                    # Snapshot everything needed to genuinely continue this
                    # exact step later — current_arguments already holds the
                    # next page number / cursor (it's advanced at the end of
                    # each successful page before the loop re-checks its
                    # budget), so resuming from here picks up new pages
                    # instead of re-fetching from page 1.
                    step_resume_snapshots[node_id] = {
                        "tool_name": tool_name,
                        "node_id": node_id,
                        "current_arguments": current_arguments,
                        "accumulated_items": accumulated_items,
                        "accumulated_raw": accumulated_raw,
                        "auto_paginated": auto_paginated,
                        "fetch_scope": fetch_scope,
                        "schema": schema,
                        "prev_cursor_value": prev_cursor_value,
                    }
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

        # Check if any step hit the pagination cap — if so, offer to continue.
        # Only the first cap-hit step is resumable per continuation round (a
        # plan with more than one exhaustive-scope step capping in the same
        # turn is rare); any others just keep whatever they'd already
        # fetched, same as before.
        cap_hit_steps = [r for r in tool_results if r.get("cap_hit")]
        if cap_hit_steps:
            resume_node_id = cap_hit_steps[0]["node_id"]
            snapshot = step_resume_snapshots.get(resume_node_id)
            if snapshot:
                self._pagination_state = {
                    "tool_results": tool_results,
                    "prior_results_map": prior_results_map,
                    **snapshot,
                }
                self.state = AgentState.WAITING_LOOP_CONTINUATION
                cap_tool_names = ", ".join(f"`{r['tool']}`" for r in cap_hit_steps)
                prompt = (
                    f"\n\n**Pagination cap reached** for {cap_tool_names}. "
                    "I've fetched as many pages as allowed but may not have the full picture yet.\n\n"
                    "**Continue fetching more pages?** Reply **'yes'** to fetch another batch or **'no'** to proceed with what I have."
                )
                yield {"text": prompt, "node_id": None, "status": "waiting"}
                return

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
