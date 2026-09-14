import json
import logging
import re
from typing import Dict, List, Optional, Any
import anyio

from app.core.llm_manager import LLMManager
from app.db.database import SessionLocal
from app.db.crud import save_entity, build_entity_context_block

from .base import BaseAgent
from .executor import ExecutorAgent
from app.prompts.chat import build_chat_prompt
from app.mcp.registry import mcp_registry

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


def _build_document_search_grammar():
    """
    Same grammar-constrained-JSON pattern as planner.py's
    _build_plan_grammar, sized for _decide_document_search's much smaller
    decision schema. Returns None (caller falls back to response_format
    json_object) if llama_cpp isn't importable or compilation fails.
    """
    try:
        from llama_cpp import LlamaGrammar
    except ImportError:
        return None

    schema = {
        "type": "object",
        "properties": {
            "needs_search": {"type": "boolean"},
            "whole_document": {"type": "boolean"},
            "query": {"type": "string"},
        },
        "required": ["needs_search", "whole_document", "query"],
    }
    try:
        return LlamaGrammar.from_json_schema(json.dumps(schema))
    except Exception as e:
        logger.warning(f"[ChatAgent] Document-search grammar compile failed, falling back to json_object mode: {e}")
        return None


# The editable half of _decide_document_search's prompt (also reused, via
# a plain generic "llm" node configured with this exact text and a
# needs_search/whole_document/query structured-output schema, as the
# seeded chat pipeline's own "Decide: need search?" step — see
# app.core.workflows.seed) — the attachment note + literal message are
# always prepended by _decide_document_search itself, live per-turn data,
# not persona/instructions a user would edit.
DEFAULT_DECIDE_SEARCH_PROMPT = """Decide whether answering this message requires looking at the content of the user's uploaded document(s) in this conversation.

Output a JSON object with three keys:
- "needs_search": true only if the document's actual content is needed to answer — not for general questions, greetings, or things answerable without it.
- "whole_document": true if the user wants the document's content broadly — summarize/describe/explain it, "what is this", "what does it say", "give me a rundown/overview/walkthrough of it", "tell me everything/everything in it" — anything asking about the document as a whole rather than one specific fact. false only for a targeted lookup of one specific detail (a clause, a number, a name, a date).
- "query": if needs_search is true and whole_document is false, a short focused search phrase capturing exactly what to look up (resolve any vague wording or pronouns using the message itself) — otherwise an empty string.

Output valid JSON only. Two examples:
Targeted lookup: {"needs_search": true, "whole_document": false, "query": "termination clause notice period"}
Whole document: {"needs_search": true, "whole_document": true, "query": ""}"""

# The editable half of _classify_export_and_compound's prompt (see
# app.core.workflows.seed's "Classify Export & Multi-part" node) — the
# literal message itself is always prepended by the caller
# (f'Message: "{message}"\n\n{prompt_override or DEFAULT_TURN_CLASSIFIER_PROMPT}'),
# not persona/instructions a user would edit.
DEFAULT_TURN_CLASSIFIER_PROMPT = """Analyze this message and answer two independent questions about it.

Output a JSON object with three keys:
- "is_export": true only if the user wants a FILE created from this conversation's content — not just a question that happens to mention a file/document, and not a request to read or open something that already exists.
- "format": one of "pdf", "docx", "xlsx" if is_export is true (closest match — e.g. "word document" -> "docx", "spreadsheet"/"excel" -> "xlsx", anything else -> "pdf"), otherwise null.
- "parts": a list of the distinct questions/requests as short strings, in order, ONLY if the message bundles 2 or more genuinely separate asks that each need their own answer. Otherwise an empty list.

Output valid JSON only. Example: {"is_export": true, "format": "docx", "parts": []}"""


# Constrains free-text generation (_call_llm_text — the actual chat answer,
# not the planner/executor's structured calls) so the response's first
# non-whitespace character can never be '{'. Chat Mode's prompt already
# instructs "NEVER output JSON" (see build_chat_prompt's rule 6-7), but a
# degraded local model sharing history with Agent Mode's plan/tool-result
# JSON blocks (rule 3) sometimes imitates that format anyway and replies
# with a bare JSON object instead of prose — this makes that shape
# structurally unsampleable rather than relying on the instruction alone.
# Deliberately narrow: only the very first character is constrained, so a
# legitimate answer that includes JSON further in (a code block, an
# example) is completely unaffected — the model just has to write
# something, anything, before it. Compiled once and cached since it's
# static, unlike the planner's per-turn tool-name-constrained grammar.
_no_json_grammar = None
_no_json_grammar_load_attempted = False


def _get_no_json_grammar():
    global _no_json_grammar, _no_json_grammar_load_attempted
    if _no_json_grammar_load_attempted:
        return _no_json_grammar
    _no_json_grammar_load_attempted = True
    try:
        from llama_cpp import LlamaGrammar
        _no_json_grammar = LlamaGrammar.from_string(
            "root ::= ws nonbrace rest\n"
            "ws ::= [ \\t\\n\\r]*\n"
            "nonbrace ::= [^{\\x00]\n"
            "rest ::= [^\\x00]*\n"
        )
    except Exception as e:
        logger.warning(f"No-JSON grammar compile failed, chat text generation falls back to unconstrained: {e}")
        _no_json_grammar = None
    return _no_json_grammar


_ocr_engine = None


def _get_ocr_engine():
    """Lazily-loaded, reused across calls — same pattern as
    app.core.transcription._get_model. rapidocr-onnxruntime bundles its own
    small detection/classification/recognition ONNX models, so this needs
    no separate download/install step."""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _count_tokens(llm, text: str) -> int:
    """
    Real token count via the active model's own tokenizer, not a
    characters-divided-by-some-constant proxy — chars-to-tokens varies
    enough (code, non-English text, punctuation-heavy text) that a fixed
    char cap either wastes context budget being overly conservative or
    occasionally lets something through that's actually too big.
    add_bos=False since this measures a content fragment being assembled
    into a larger prompt, not a full standalone prompt.
    """
    if not text:
        return 0
    try:
        return len(llm.tokenize(text.encode("utf-8", errors="ignore"), add_bos=False))
    except Exception:
        return len(text) // 4  # same rough proxy as before, only as a last resort


def _trim_history_to_token_budget(llm, system_content: str, history: List[Dict], reply_buffer: int = 600) -> List[Dict]:
    """
    Final safety net right before generation. The per-message character
    caps applied earlier (_MAX_CHAT_MSG_CHARS / _MAX_MSG_CHARS) bound each
    message individually, but not the *sum* of everything, and character
    count is only a rough proxy for token count in the first place — the
    total could still exceed the model's real context window despite every
    individual cap being respected. This measures the actual total with
    the model's own tokenizer and, if still over budget, drops the OLDEST
    history messages first — history is the most expendable part of
    context — until it fits, leaving reply_buffer tokens of
    headroom for the model's actual answer. The current turn (always the
    last message here — see _handle_idle) is never dropped even if that
    alone leaves things tight: cutting off the user's actual question is
    worse than a rare context-limit error.

    Mutates and returns `history` in place.
    """
    if not history:
        return history
    try:
        budget = llm.n_ctx() - reply_buffer
    except Exception:
        return history  # can't measure the real window — leave the char caps as the only line of defense

    def _total() -> int:
        total = _count_tokens(llm, system_content) + 4  # +4: rough per-message role/template overhead
        for m in history:
            total += _count_tokens(llm, m.get("content", "")) + 4
        return total

    while len(history) > 1 and _total() > budget:
        history.pop(0)
    return history


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


class AgentState:
    """Vestigial now that Agent Mode's plan/confirm/execute loop is gone (replaced
    by user-designed workflows — see app.core.workflows.engine) — kept as a single
    value because external code (scheduler.py, websocket.py) still checks
    session.state == "IDLE" as a cheap "is this session mid-turn" signal, backed
    by is_processing rather than a real state machine now."""
    IDLE = "IDLE"


class ChatAgent(BaseAgent):
    def __init__(self, connection_id: str):
        import time
        llm_mgr = get_llm_manager()
        super().__init__(llm_mgr)
        self.connection_id = connection_id
        self._state = AgentState.IDLE
        self.state_entered_at = time.time()
        self.is_processing = False

        # Fix 1: Session-wide monotonically increasing step counter.
        # Ensures step IDs are globally unique across turns (e.g. t1_step_1, t2_step_1)
        # so the Planner never confuses a stale step reference from chat history
        # with a live step in the current plan.
        self._turn_counter: int = 0

        # Fix 3: Structured recent tool results injected into the Planner context.
        # Keyed by tool_name -> truncated result string. Cleared each new turn.
        # This bypasses prose chat history entirely for the "act on what I just found" case.
        self._last_tool_results: List[Dict] = []  # [{tool, result_snippet}]

        # RAG chunks actually used to answer the current Chat Mode turn, if
        # any — set inside _get_document_context, read back in _handle_idle
        # to persist alongside the assistant's reply (rag_sources_json) so a
        # later turn's empty/weak search can backfill from them. Reset at
        # the start of every _get_document_context call, same lifecycle as
        # _last_tool_results above.
        self._last_rag_sources: Optional[List[Dict]] = None

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

        # Instantiate sub-agents. PlannerAgent/Agent Mode's plan/confirm/
        # execute state machine was removed (replaced by user-designed
        # workflows — see app.core.workflows.engine); ExecutorAgent is kept
        # since run_chat_workflow still uses that class directly (its own
        # instance, not this one).
        self.executor = ExecutorAgent(llm_mgr)
        self.executor.cancel_event = self.cancel_event

    async def _append_history(
        self, role: str, content: str, attachments: Optional[List[Dict]] = None, msg_type: Optional[str] = None,
        rag_sources: Optional[List[Dict]] = None,
    ):
        """Asynchronously persist a chat message to SQLite via db_executor."""
        import asyncio
        loop = asyncio.get_running_loop()

        def _write():
            from app.db.database import SessionLocal
            from app.db.crud import add_chat_message
            db = SessionLocal()
            try:
                add_chat_message(db, self.connection_id, role, content, attachments=attachments, msg_type=msg_type, rag_sources=rag_sources)
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
              EXAMPLES:
                "<a realistic user phrasing>" -> arguments: {"arg1": "..."}

        EXAMPLES is opt-in per tool (only local tools carry one so far — see
        _get_filesystem_tool_defs) rather than derived automatically: a
        schema alone tells a small local model WHAT the arguments are, not
        HOW a real user phrasing maps to concrete values for THIS specific
        tool, which is exactly the gap a couple of real prompt->arguments
        pairs closes. Same technique AnythingLLM's aibitat tool plugins use
        (each tool ships its own few-shot `examples` array) — verified
        directly against their rag-memory tool definition.
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

        examples = t.get("examples") or []
        if examples:
            lines.append("  EXAMPLES:")
            for ex in examples:
                prompt = ex.get("prompt", "")
                args_json = json.dumps(ex.get("arguments", {}))
                lines.append(f'    "{prompt}" -> arguments: {args_json}')

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
    def list_local_tools_for_palette() -> List[Dict]:
        """
        Local tools for the workflow canvas's node palette (app.api.workflows) —
        every local tool, gated only on Chromium actually being installed, not
        on the per-conversation "Tools switch" toggle _get_scraper_tools also
        checks (there is no conversation for a workflow to belong to; a node
        the user explicitly dragged onto their canvas is opt-in by
        construction, the same way the toggle exists to let a user opt out
        in Chat Mode).
        """
        from app.core.scraper import is_chromium_installed
        scraper_tools = ChatAgent._scraper_tool_defs() if is_chromium_installed() else []
        return scraper_tools + ChatAgent._get_filesystem_tool_defs() + ChatAgent._extraction_tool_defs()

    @staticmethod
    def _extraction_tool_defs() -> List[Dict]:
        """
        Lightweight extraction tools — unlike web_scrape/browser_* these need
        no install step at all: extract_webpage_text is a plain HTTP GET (no
        headless browser, so it can't handle JS-rendered pages the way
        web_scrape can — it's the fast path for ordinary static pages),
        transcribe_media reuses the already-bundled faster-whisper model
        (app.core.transcription, same one the mic composer uses) against a
        file instead of a live stream, and extract_image_text runs a small
        bundled OCR model (rapidocr-onnxruntime — a few MB, onnxruntime-only,
        no system binary — see app.core.agents.chat._execute_extract_image_text)
        for printed/on-screen text in images, which nothing else in this app
        does (uploaded images are vision-only, see app.core.rag.processor).
        Always available, same as the filesystem tools — no marketplace
        install/status gating.
        """
        return [
            {
                "name": "extract_webpage_text",
                "description": (
                    "Fetches a PUBLIC static web page over plain HTTP and extracts its "
                    "main readable text (article/body content, not navigation/ads/"
                    "boilerplate) — no headless browser, so it's fast but can't render "
                    "JavaScript-built pages (use web_scrape for those instead). Use for "
                    "a quick read of an ordinary article/blog/docs page."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The full URL to fetch, including https://"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "transcribe_media",
                "description": (
                    "Transcribes speech in a local audio or video file (mp3, wav, m4a, "
                    "mp4, mov, ...) into text, entirely on-device. Use when the user "
                    "wants a transcript of a recording, meeting, voice note, or video's "
                    "spoken content."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Absolute path to the local audio/video file."},
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "extract_image_text",
                "description": (
                    "Runs OCR (optical character recognition) on a local image to pull "
                    "out any printed or on-screen text — a scanned page, a screenshot, a "
                    "photo of a sign or document. Use when the user needs the literal "
                    "text inside an image rather than a description of what's in it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Absolute path to the local image file."},
                    },
                    "required": ["file_path"],
                },
            },
        ]

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
                "examples": [
                    {"prompt": "find my resume", "arguments": {"query": "resume"}},
                    {"prompt": "look for PDFs in Downloads from this year", "arguments": {"query": "", "root": "Downloads", "extension": "pdf", "modified_after": "2026-01-01"}},
                ],
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
                "examples": [
                    {"prompt": "what's in my Downloads folder?", "arguments": {"path": "Downloads"}},
                ],
            },
            {
                "name": "read_file",
                "description": (
                    "Reads and extracts the text content of a specific file on "
                    "the user's laptop by its exact path (get the path from "
                    "search_local_files or list_folder first if you don't already "
                    "have it) — confined to their home directory, credential-shaped "
                    "files refused. Supports PDF, DOCX, XLSX, PPTX, CSV, and plain "
                    "text/markdown — the same extraction used for files the user "
                    "uploads to chat, so a file already on disk doesn't need to be "
                    "manually uploaded first. Images and audio/video files are not "
                    "supported (no OCR or transcription fallback). Long "
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
                "examples": [
                    {"prompt": "read report.pdf on my desktop", "arguments": {"path": "Desktop/report.pdf"}},
                ],
            },
            {
                "name": "write_file",
                "description": (
                    "Writes content to a file on the user's laptop, confined to "
                    "their home directory. Fails if the file already exists unless "
                    "`overwrite` is explicitly set true — never silently replaces "
                    "an existing file. Use when the user asks you to save something "
                    "(a summary, a list, generated text) to a real file on disk. "
                    "Plain text (`encoding` omitted or 'text') covers most cases — "
                    "for a binary file (e.g. placing a PDF/DOCX you generated), set "
                    "`encoding` to 'base64' and pass the file's base64-encoded bytes "
                    "as `content`."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Destination file path, relative to the home directory or absolute. Parent folders are created if needed."},
                        "content": {"type": "string", "description": "The content to write — plain text, or base64-encoded bytes when encoding='base64'."},
                        "overwrite": {"type": "boolean", "description": "Set true to replace an existing file at that path. Defaults to false (fails instead of clobbering)."},
                        "encoding": {"type": "string", "enum": ["text", "base64"], "description": "'text' (default) writes `content` as UTF-8 text. 'base64' decodes `content` and writes the raw bytes — use for PDFs, images, or any other binary file."},
                    },
                    "required": ["path", "content"],
                },
                "examples": [
                    {"prompt": "save this summary to notes.txt in Documents", "arguments": {"path": "Documents/notes.txt", "content": "<the summary text>"}},
                ],
            },
            {
                "name": "copy_file",
                "description": (
                    "Copies a single file on the user's laptop from one path to "
                    "another, both confined to their home directory. Fails if the "
                    "destination already exists unless `overwrite` is explicitly "
                    "set true. Use for 'copy this file to that folder' — the "
                    "original at `src` is left in place. Directories are not "
                    "supported, only single files."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "Path of the existing file to copy, relative to the home directory or absolute."},
                        "dst": {"type": "string", "description": "Destination file path. Parent folders are created if needed."},
                        "overwrite": {"type": "boolean", "description": "Set true to replace an existing file at dst. Defaults to false."},
                    },
                    "required": ["src", "dst"],
                },
                "examples": [
                    {"prompt": "copy report.pdf from Downloads to Documents", "arguments": {"src": "Downloads/report.pdf", "dst": "Documents/report.pdf"}},
                ],
            },
            {
                "name": "move_file",
                "description": (
                    "Moves (or renames) a single file on the user's laptop from "
                    "one path to another, both confined to their home directory. "
                    "Fails if the destination already exists unless `overwrite` is "
                    "explicitly set true. Use for 'move this file to that folder' "
                    "or 'rename this file' — the source no longer exists at `src` "
                    "afterward. Directories are not supported, only single files."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "Path of the existing file to move, relative to the home directory or absolute."},
                        "dst": {"type": "string", "description": "Destination file path. Parent folders are created if needed."},
                        "overwrite": {"type": "boolean", "description": "Set true to replace an existing file at dst. Defaults to false."},
                    },
                    "required": ["src", "dst"],
                },
                "examples": [
                    {"prompt": "move budget.xlsx to the Archive folder", "arguments": {"src": "budget.xlsx", "dst": "Archive/budget.xlsx"}},
                ],
            },
            {
                "name": "delete_file",
                "description": (
                    "Permanently deletes a single file on the user's laptop, "
                    "confined to their home directory — there is no trash/recycle "
                    "bin and no undo. Directories are not supported, only single "
                    "files. Only use this when the user has clearly and "
                    "specifically asked to delete or remove a file."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Exact file path to delete, relative to the home directory or absolute."},
                    },
                    "required": ["path"],
                },
                "examples": [
                    {"prompt": "delete old_notes.txt", "arguments": {"path": "old_notes.txt"}},
                ],
            },
            {
                "name": "export_file",
                "description": (
                    "Renders markdown content as a real PDF, DOCX, or XLSX file and "
                    "saves it directly to a path on the user's laptop, confined to "
                    "their home directory — proper formatting (headings, tables, "
                    "lists), not a plain-text dump. Distinct from write_file: this "
                    "one converts the content for you, so pass plain markdown as "
                    "`content`, not base64. Fails if the destination already exists "
                    "unless `overwrite` is explicitly set true. Use whenever the "
                    "user wants generated content (a report, a summary, a table) "
                    "saved as an actual PDF/DOCX/XLSX file on disk, as opposed to "
                    "Chat Mode's export-to-download-link."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The exact markdown content to render into the file (headings, lists, tables, plain paragraphs)."},
                        "format": {"type": "string", "enum": ["pdf", "docx", "xlsx"], "description": "Output file format."},
                        "path": {"type": "string", "description": "Destination file path (include the matching extension, e.g. 'Documents/report.pdf'), relative to the home directory or absolute. Parent folders are created if needed."},
                        "title": {"type": "string", "description": "Optional document title. Omit if `content` already starts with its own top-level heading."},
                        "overwrite": {"type": "boolean", "description": "Set true to replace an existing file at that path. Defaults to false (fails instead of clobbering)."},
                    },
                    "required": ["content", "format", "path"],
                },
                "examples": [
                    {"prompt": "export this summary as a PDF to Documents/summary.pdf", "arguments": {"content": "<the summary content, in markdown>", "format": "pdf", "path": "Documents/summary.pdf"}},
                ],
            },
        ]

    def _get_scraper_tools(self) -> List[Dict]:
        """Chromium-installed + per-conversation capability-toggle gated — see
        _scraper_tool_defs for the same defs without either gate (used by the
        workflow node palette, which has no per-conversation toggle to check)."""
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
            return self._scraper_tool_defs()
        except Exception as e:
            logger.warning(f"Failed to resolve local tools: {e}")
            return []

    @staticmethod
    def _scraper_tool_defs() -> List[Dict]:
        """web_scrape + the browser_* tool definitions, no gating — see
        _get_scraper_tools for the Chat-Mode-facing, gated version."""
        try:
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
            }, *ChatAgent._browser_tool_defs()]
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
        servers' tools plus local tools (web_scrape, browser_*, filesystem,
        export). EVERY consumer that needs the full tool list (plan
        validation, execution, prompt building, the query-rewrite pass)
        MUST call this rather than reconstructing the list by hand, so a
        newly connected/disconnected MCP server is picked up everywhere at
        once. With zero MCP servers connected, mcp_registry.list_all_tools()
        returns [] and this is identical to local-tools-only.
        """
        return mcp_registry.list_all_tools() + self._get_local_tools()


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
        Dispatch for the web_scrape local tool — called by a workflow run
        (app.core.workflows.engine) when a node is wired to this tool; Chat
        Mode does no tool calling at all (see _handle_idle, which nudges the
        user to build a Workflow on a detected URL instead). Deliberately
        ephemeral: no persistence beyond that run's own output — no
        UserDocument row, no Qdrant embedding, no Files panel entry. Only
        files the user explicitly uploads go into the vector DB; see
        app.core.scraper.scrape_url, which this calls directly with no
        ingestion pipeline involved.

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

    async def _execute_extract_webpage(self, arguments: Dict) -> Dict:
        """
        extract_webpage_text — a plain HTTP GET + trafilatura extraction, no
        headless browser. Deliberately separate from _execute_web_scrape:
        this is the fast, no-Chromium path for ordinary static pages; a
        JS-rendered page will come back empty/garbled here and needs
        web_scrape instead. Same ephemeral-only contract as web_scrape — no
        persistence, no Qdrant embedding.
        """
        import httpx
        import trafilatura

        url = arguments.get("url", "")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            return {"success": False, "text": "", "error": str(e)}

        text = await anyio.to_thread.run_sync(lambda: trafilatura.extract(html, favor_recall=True) or "")
        return {"success": bool(text), "text": text, "error": None if text else "No readable text found on that page."}

    async def _execute_transcribe_media(self, arguments: Dict) -> Dict:
        """
        transcribe_media — reuses the already-bundled faster-whisper model
        (app.core.transcription, the same one the mic composer uses) against
        a file path instead of a live recording. faster-whisper decodes
        audio from a video container directly, so this covers both audio and
        video files with no separate extraction step.
        """
        from app.core.transcription import transcribe, is_installed

        file_path = arguments.get("file_path", "")
        if not is_installed():
            return {"success": False, "text": "", "error": "Transcription model isn't available in this build."}
        try:
            text = await anyio.to_thread.run_sync(transcribe, file_path)
            return {"success": True, "text": text, "error": None}
        except Exception as e:
            return {"success": False, "text": "", "error": str(e)}

    async def _execute_extract_image_text(self, arguments: Dict) -> Dict:
        """
        extract_image_text — OCR via rapidocr-onnxruntime (bundled, no
        system binary, no separate marketplace install/download — see
        ChatAgent._extraction_tool_defs). The one lazily-loaded module-level
        engine instance is reused across calls, same pattern as
        app.core.transcription._get_model.
        """
        file_path = arguments.get("file_path", "")
        try:
            result, _ = await anyio.to_thread.run_sync(lambda: _get_ocr_engine()(file_path))
        except Exception as e:
            return {"success": False, "text": "", "error": str(e)}

        text = "\n".join(line[1] for line in result) if result else ""
        return {"success": bool(text), "text": text, "error": None if text else "No text detected in that image."}

    _FILESYSTEM_TOOL_NAMES = {
        "search_local_files", "list_folder", "read_file",
        "write_file", "copy_file", "move_file", "delete_file", "export_file",
    }
    # Every filesystem tool except the write/copy/move/delete ones only ever
    # looks at the disk — used for the plan-confirmation card's
    # [read-only]/[writes] badge.
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
            search_files, list_folder, read_file_text, write_file,
            copy_file, move_file, delete_file, SandboxError,
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
                    encoding=arguments.get("encoding", "text"),
                ))

            if tool_name == "copy_file":
                return await anyio.to_thread.run_sync(lambda: copy_file(
                    src=arguments.get("src", ""),
                    dst=arguments.get("dst", ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                ))

            if tool_name == "move_file":
                return await anyio.to_thread.run_sync(lambda: move_file(
                    src=arguments.get("src", ""),
                    dst=arguments.get("dst", ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                ))

            if tool_name == "delete_file":
                return await anyio.to_thread.run_sync(lambda: delete_file(arguments.get("path", "")))

            if tool_name == "export_file":
                return await self._execute_export_file(arguments)

            return {"success": False, "error": f"Unknown filesystem tool '{tool_name}'."}
        except SandboxError as e:
            return {"success": False, "error": str(e)}

    async def _execute_export_file(self, arguments: Dict) -> Dict:
        """
        Dispatch for export_file: renders markdown -> PDF/DOCX/XLSX bytes via
        app.core.exporter (the same renderer Chat Mode's export-to-download-
        link uses), then hands the bytes to filesystem_tools.write_file's
        base64 path for the actual sandboxed write — reusing its overwrite/
        denylist/containment checks rather than duplicating them here.
        """
        import base64
        from app.core.exporter import export_markdown, CONTENT_TYPES
        from app.core.filesystem_tools import write_file, SandboxError

        content = arguments.get("content", "")
        fmt = (arguments.get("format") or "").lower().strip()
        path = arguments.get("path", "")
        title = arguments.get("title", "")

        if fmt not in CONTENT_TYPES:
            return {"success": False, "error": f"Unsupported format '{fmt}'. Use pdf, docx, or xlsx."}
        if not content.strip():
            return {"success": False, "error": "Nothing to export — content was empty."}

        try:
            data = await anyio.to_thread.run_sync(export_markdown, content, fmt, title)
        except Exception as e:
            return {"success": False, "error": f"Export failed: {e}"}

        try:
            result = await anyio.to_thread.run_sync(lambda: write_file(
                path=path,
                content=base64.b64encode(data).decode(),
                overwrite=bool(arguments.get("overwrite", False)),
                encoding="base64",
            ))
        except SandboxError as e:
            return {"success": False, "error": str(e)}

        if result.get("success"):
            result["format"] = fmt
        return result

    async def _execute_export_file(self, arguments: Dict) -> Dict:
        """
        Dispatch for export_file: renders markdown -> PDF/DOCX/XLSX bytes via
        app.core.exporter (the same renderer Chat Mode's export-to-download-
        link uses), then hands the bytes to filesystem_tools.write_file's
        base64 path for the actual sandboxed write — reusing its overwrite/
        denylist/containment checks rather than duplicating them here.
        """
        import base64
        from app.core.exporter import export_markdown, CONTENT_TYPES
        from app.core.filesystem_tools import write_file, SandboxError

        content = arguments.get("content", "")
        fmt = (arguments.get("format") or "").lower().strip()
        path = arguments.get("path", "")
        title = arguments.get("title", "")

        if fmt not in CONTENT_TYPES:
            return {"success": False, "error": f"Unsupported format '{fmt}'. Use pdf, docx, or xlsx."}
        if not content.strip():
            return {"success": False, "error": "Nothing to export — content was empty."}

        try:
            data = await anyio.to_thread.run_sync(export_markdown, content, fmt, title)
        except Exception as e:
            return {"success": False, "error": f"Export failed: {e}"}

        try:
            result = await anyio.to_thread.run_sync(lambda: write_file(
                path=path,
                content=base64.b64encode(data).decode(),
                overwrite=bool(arguments.get("overwrite", False)),
                encoding="base64",
            ))
        except SandboxError as e:
            return {"success": False, "error": str(e)}

        if result.get("success"):
            result["format"] = fmt
        return result

    _FILESYSTEM_TOOL_NAMES = {
        "search_local_files", "list_folder", "read_file",
        "write_file", "copy_file", "move_file", "delete_file", "export_file",
    }
    # Every filesystem tool except the write/copy/move/delete ones only ever
    # looks at the disk — used for the plan-confirmation card's
    # [read-only]/[writes] badge.
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
            search_files, list_folder, read_file_text, write_file,
            copy_file, move_file, delete_file, SandboxError,
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
                    encoding=arguments.get("encoding", "text"),
                ))

            if tool_name == "copy_file":
                return await anyio.to_thread.run_sync(lambda: copy_file(
                    src=arguments.get("src", ""),
                    dst=arguments.get("dst", ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                ))

            if tool_name == "move_file":
                return await anyio.to_thread.run_sync(lambda: move_file(
                    src=arguments.get("src", ""),
                    dst=arguments.get("dst", ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                ))

            if tool_name == "delete_file":
                return await anyio.to_thread.run_sync(lambda: delete_file(arguments.get("path", "")))

            if tool_name == "export_file":
                return await self._execute_export_file(arguments)

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
        deterministic export-intent check in _handle_idle) isn't an HTTP
        handler, so there's no request/response cycle to hand raw file bytes
        back through. The returned download_url is what actually lets the
        user get the file: an absolute link to this backend's own
        /api/export/download/{id} route, rendered as a normal markdown link
        in the chat message.

        Not offered as a workflow-node tool (see _get_local_tools' docstring
        for why) — called directly as a plain function from Chat Mode's own
        export-intent handling instead.
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

    # Deliberately loose — this only gates whether the LLM classification
    # call below runs at all, not whether export actually fires. A single
    # hint word is enough to spend one small LLM call finding out; the
    # overwhelming majority of messages (greetings, questions, coding asks)
    # match none of these and skip the call entirely, at zero cost.
    _EXPORT_HINT_RE = re.compile(
        r'\b(export|download|save|convert|pdf|docx?|xlsx|excel|word|spreadsheet|file)\b',
        re.IGNORECASE,
    )

    # "What is this", "describe/summarize the PDF", "what's in this file" —
    # requests about the document AS A WHOLE, not a specific fact in it.
    # These have essentially no content of their own to embed, so semantic/
    # keyword retrieval has nothing real to match against — empirically
    # verified these score in the same -8 to -11 rerank range as a
    # genuinely off-topic query (see hybrid_search's _MIN_RERANK_SCORE),
    # meaning top-k retrieval for a request like this either returns
    # near-random chunks or, with that cutoff in place, nothing at all.
    # _get_document_context checks this before running hybrid_search at
    # all, and hands over the full extracted text instead when it fits.
    _WHOLE_DOCUMENT_INTENT_RE = re.compile(
        r"what(?:'s|\s+is)\s+(?:this|it)\b"
        r"|\bdescribe\s+(?:this|the)\s+(?:pdf|document|file|doc|spreadsheet|report)\b"
        r"|\bsummar(?:y|ize|ise)\b"
        r"|\boverview\b"
        r"|what\s+does\s+(?:this|it)\s+(?:say|contain|cover)\b"
        r"|what'?s\s+in\s+(?:this|the)\s+(?:file|document|pdf|doc)\b"
        r"|\bexplain\s+(?:this|the)\s+(?:document|file|pdf|doc)\b"
        r"|tell\s+me\s+about\s+(?:this|the)\s+(?:document|file|pdf|doc)\b",
        re.IGNORECASE,
    )

    # ~40K chars (~10K tokens) comfortably fits alongside history/system
    # prompt even in a small local model's context window (e.g. the 32K
    # default), while still being generous for a real document — a
    # novel-length upload correctly falls through to top-k retrieval
    # instead of silently truncating and claiming it's complete.
    _WHOLE_DOCUMENT_MAX_CHARS = 40_000

    async def _whole_document_context(self, attached_ids: List[int], vision_gap_note: str) -> Optional[str]:
        """
        Full-extracted-text fallback for _get_document_context's
        whole-document-intent branch. Returns None (meaning: fall through
        to normal top-k retrieval) if no attached document is ready yet,
        extraction fails, or the combined text is too large to inject
        whole — never partial/truncated silently, since that would look
        complete to the model while actually missing content.
        """
        from app.db.models import UserDocument
        from app.core.rag.processor import extract_text
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()

        _db = SessionLocal()
        try:
            rows = _db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
        finally:
            _db.close()

        ready_rows = [r for r in rows if r.status == "ready"]
        if not ready_rows:
            return None

        sections = []
        total_chars = 0
        for row in ready_rows:
            try:
                text = await loop.run_in_executor(db_executor, extract_text, row.file_path, row.file_type)
            except Exception:
                return None
            total_chars += len(text)
            if total_chars > self._WHOLE_DOCUMENT_MAX_CHARS:
                return None
            sections.append((row.filename, text))

        if not sections:
            return None

        block = vision_gap_note + "Full content of your uploaded document(s):\n\n"
        for filename, text in sections:
            block += f"--- Source: {filename} ---\n{text}\n\n"
        return block

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

    # Deliberately loose — this only gates whether the LLM classification
    # call below runs at all, not whether export actually fires. A single
    # hint word is enough to spend one small LLM call finding out; the
    # overwhelming majority of messages (greetings, questions, coding asks)
    # match none of these and skip the call entirely, at zero cost.
    _EXPORT_HINT_RE = re.compile(
        r'\b(export|download|save|convert|pdf|docx?|xlsx|excel|word|spreadsheet|file)\b',
        re.IGNORECASE,
    )

    # "What is this", "describe/summarize the PDF", "what's in this file" —
    # requests about the document AS A WHOLE, not a specific fact in it.
    # These have essentially no content of their own to embed, so semantic/
    # keyword retrieval has nothing real to match against — empirically
    # verified these score in the same -8 to -11 rerank range as a
    # genuinely off-topic query (see hybrid_search's _MIN_RERANK_SCORE),
    # meaning top-k retrieval for a request like this either returns
    # near-random chunks or, with that cutoff in place, nothing at all.
    # _get_document_context checks this before running hybrid_search at
    # all, and hands over the full extracted text instead when it fits.
    _WHOLE_DOCUMENT_INTENT_RE = re.compile(
        r"what(?:'s|\s+is)\s+(?:this|it)\b"
        r"|\bdescribe\s+(?:this|the)\s+(?:pdf|document|file|doc|spreadsheet|report)\b"
        r"|\bsummar(?:y|ize|ise)\b"
        r"|\boverview\b"
        r"|what\s+does\s+(?:this|it)\s+(?:say|contain|cover)\b"
        r"|what'?s\s+in\s+(?:this|the)\s+(?:file|document|pdf|doc)\b"
        r"|\bexplain\s+(?:this|the)\s+(?:document|file|pdf|doc)\b"
        r"|tell\s+me\s+about\s+(?:this|the)\s+(?:document|file|pdf|doc)\b",
        re.IGNORECASE,
    )

    # ~40K chars (~10K tokens) comfortably fits alongside history/system
    # prompt even in a small local model's context window (e.g. the 32K
    # default), while still being generous for a real document — a
    # novel-length upload correctly falls through to top-k retrieval
    # instead of silently truncating and claiming it's complete.
    _WHOLE_DOCUMENT_MAX_CHARS = 40_000

    async def _whole_document_context(self, attached_ids: List[int], vision_gap_note: str) -> Optional[str]:
        """
        Full-extracted-text fallback for _get_document_context's
        whole-document-intent branch. Returns None (meaning: fall through
        to normal top-k retrieval) if no attached document is ready yet,
        extraction fails, or the combined text is too large to inject
        whole — never partial/truncated silently, since that would look
        complete to the model while actually missing content.
        """
        from app.db.models import UserDocument
        from app.core.rag.processor import extract_text
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()

        _db = SessionLocal()
        try:
            rows = _db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
        finally:
            _db.close()

        ready_rows = [r for r in rows if r.status == "ready"]
        if not ready_rows:
            return None

        sections = []
        total_chars = 0
        for row in ready_rows:
            try:
                text = await loop.run_in_executor(db_executor, extract_text, row.file_path, row.file_type)
            except Exception:
                return None
            total_chars += len(text)
            if total_chars > self._WHOLE_DOCUMENT_MAX_CHARS:
                return None
            sections.append((row.filename, text))

        if not sections:
            return None

        block = vision_gap_note + "Full content of your uploaded document(s):\n\n"
        for filename, text in sections:
            block += f"--- Source: {filename} ---\n{text}\n\n"
        return block

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

    def _classify_export_and_compound(
        self, message: str, prompt_override: Optional[str] = None, model_name: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[List[str]]]:
        """
        Combined variant of _classify_export_intent + _decompose_compound_
        question — one LLM prefill answering both questions instead of two
        serialized ones. At _handle_idle's call site, only used when BOTH
        cheap gates (_EXPORT_HINT_RE and the compound-question pair) fire
        on the same message; when only one fires, that single-purpose
        method is called directly there instead, since it's cheaper AND
        more reliable for a small model than asking a combined prompt it
        didn't need to answer. app.core.workflows.engine's "turn_classifier"
        node, by contrast, always calls this one method when any call is
        needed — one prompt to configure there, not three.

        prompt_override/model_name let that node use a custom prompt/
        model; _handle_idle never passes either, so its behavior is
        unchanged (DEFAULT_TURN_CLASSIFIER_PROMPT, active model).
        """
        llm = self.get_llm(model_name)
        if not llm:
            return None, None

        prompt = f'Message: "{message}"\n\n{prompt_override or DEFAULT_TURN_CLASSIFIER_PROMPT}'

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

    def get_available_tools(self, query: str = "") -> str:
        """
        Tool listing for Chat Mode's system prompt ("TOOLS CURRENTLY
        AVAILABLE IN THIS CHAT (for awareness only)" — see build_chat_prompt).
        Delegates to get_searched_tools' own capped/relevance-scoped list
        rather than dumping every connected tool unfiltered — measured live
        with a single real-world MCP connection (Google Drive, 116 tools):
        an unscoped dump here blew Chat Mode's own context window on a
        plain "what is 12 + 30?" message that has nothing to do with any
        tool at all, the same failure mode the planner's tool list had
        before this fix, just hitting Chat Mode's plain answer generation
        this time instead of tool selection.
        """
        tools_str, _, _ = self.get_searched_tools(query)
        return tools_str or "No tools are currently active."

    # Cheap gate replacing an LLM call: an earlier version of this ranked
    # the 1-5 most relevant tools via an LLM pass before handing them to
    # the planner, back when the curated MCP catalog could connect many
    # servers' tools at once. get_searched_tools below hands the planner
    # every tool directly UNLESS that would overflow its context budget —
    # see _MAX_TOOLS_FOR_PLANNER below, added after a single real-world MCP
    # server (a Google Drive connector, 116 tools) measured at ~23,000
    # prompt tokens against this app's 8192-token safe cap: grammar-
    # constrained plan generation failed, the ungrammared retry failed the
    # same way, and _handle_idle's last-resort fallback kicked in — a bare
    # chat completion with NO tool list at all, which is exactly when the
    # model started hallucinating plausible-looking tool names (it had
    # nothing real to pick from) instead of calling any of the 116 real
    # ones. is_counting still needs detecting (it flips list/search steps
    # to fetch_scope "exhaustive"), just via regex instead of a now-
    # pointless LLM call.
    _COUNTING_HINT_RE = re.compile(
        r'\b(how many|how much|count of|total number|number of|list all|all of the|every file|everything in|complete list)\b',
        re.IGNORECASE,
    )

    # Rough budget, not a precise token count: ~200 tokens/tool observed
    # for the verbose per-tool block (REQUIRED/OPTIONAL args, USE WHEN,
    # EXAMPLES) that _format_tool_for_planner produces, times a cap here
    # that leaves real room in an 8192-token window for the rest of the
    # planner prompt (system instructions, history, the current message).
    _MAX_TOOLS_FOR_PLANNER = 20

    def get_searched_tools(self, query: str) -> tuple[str, bool, List[str]]:
        """
        Returns the tools to show for this turn, plus a cheap regex-based
        is_counting flag (unused now that Agent Mode's planner is gone, but
        cheap enough to leave computed — see _COUNTING_HINT_RE's comment).
        Local tools are always included (there are only a handful);
        MCP-connected tools are included in full UNLESS the combined count
        would overflow the context budget (_MAX_TOOLS_FOR_PLANNER), in
        which case mcp_registry.search_tools' FTS5 index narrows them to
        whichever are actually relevant to `query`. Used by
        get_available_tools, Chat Mode's own "tools for awareness" listing
        (the workflow node palette in app.api.workflows/app.core.workflows.engine
        shows the full unscoped list instead — a human picking one tool from
        a UI list has no context-budget concern the way a prompt does).

        Kept as its own method (rather than inlining at call sites) since
        callers still expect this three-item shape.
        """
        local_tools = self._get_local_tools()
        mcp_tools = mcp_registry.list_all_tools()
        is_counting = bool(self._COUNTING_HINT_RE.search(query))

        if len(local_tools) + len(mcp_tools) > self._MAX_TOOLS_FOR_PLANNER:
            mcp_budget = max(0, self._MAX_TOOLS_FOR_PLANNER - len(local_tools))
            mcp_tools = mcp_registry.search_tools(query, top_k=mcp_budget) if mcp_budget else []

        all_tools = mcp_tools + local_tools
        if not all_tools:
            return "", False, []
        tools_str = "\n".join(self._format_tool_for_planner(t) for t in all_tools)
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

    async def handle_message(self, message: str, token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None, export_format: Optional[str] = None) -> str:
        """Entry point. Agent Mode's LLM-driven plan/confirm/execute state
        machine has been removed entirely — replaced by user-designed
        workflows (see app.core.workflows.engine). This is a thin
        pass-through now, kept as its own method since websocket.py calls
        it by this name."""
        return await self._handle_idle(
            message, token_callback=token_callback, status_callback=status_callback,
            attachments=attachments, export_format=export_format,
        )

    def _decide_document_search(
        self, message: str, attached_ids: List[int],
        prompt_override: Optional[str] = None, model_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Replaces _WHOLE_DOCUMENT_INTENT_RE plus "always hybrid_search the
        raw message verbatim" with an actual LLM judgment call: does this
        turn need document content at all, is it a whole-document request,
        and if it's a targeted lookup, what's the best focused query to
        search for — resolving vague/pronoun-laden phrasing the raw
        message alone wouldn't retrieve well. Same cheap synchronous
        classifier pattern as _classify_export_intent; callers run this via
        llm_executor, not directly (it makes a blocking LLM call).

        prompt_override/model_name let a caller that exposes this as its
        own configurable node (app.core.workflows.engine's
        "decide_document_search") use a custom prompt/model — the built-in
        _handle_idle path never passes either, so its behavior is
        completely unchanged (DEFAULT_DECIDE_SEARCH_PROMPT, active model).

        Returns None on any failure (LLM not loaded, grammar/JSON failure)
        so the caller falls back to the old regex+raw-query behavior
        exactly — this call can only improve on that baseline, never
        regress below it.
        """
        llm = self.get_llm(model_name)
        if not llm:
            return None

        attachment_note = ""
        if attached_ids:
            from app.db.models import UserDocument
            _db = SessionLocal()
            try:
                names = [
                    d.filename for d in
                    _db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
                ]
            finally:
                _db.close()
            if names:
                attachment_note = f"Attached this turn: {', '.join(names)}.\n"

        prompt = f'{attachment_note}Message: "{message}"\n\n{prompt_override or DEFAULT_DECIDE_SEARCH_PROMPT}'

        kwargs = dict(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
        )
        grammar = _build_document_search_grammar()
        if grammar is not None:
            kwargs["grammar"] = grammar
        else:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            try:
                response = llm.create_chat_completion(**kwargs)
            except Exception as e:
                if grammar is not None:
                    logger.warning(f"Document-search grammar generation failed, retrying without it: {e}")
                    kwargs.pop("grammar", None)
                    kwargs["response_format"] = {"type": "json_object"}
                    response = llm.create_chat_completion(**kwargs)
                else:
                    raise

            content = response["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            return {
                "needs_search": bool(data.get("needs_search")),
                "whole_document": bool(data.get("whole_document")),
                "query": str(data.get("query") or "").strip(),
            }
        except Exception as e:
            logger.warning(f"Document-search decision failed: {e}")
            return None

    def _decide_document_search(self, message: str, attached_ids: List[int]) -> Optional[Dict[str, Any]]:
        """
        Replaces _WHOLE_DOCUMENT_INTENT_RE plus "always hybrid_search the
        raw message verbatim" with an actual LLM judgment call: does this
        turn need document content at all, is it a whole-document request,
        and if it's a targeted lookup, what's the best focused query to
        search for — resolving vague/pronoun-laden phrasing the raw
        message alone wouldn't retrieve well. Same cheap synchronous
        classifier pattern as _classify_export_intent; callers run this via
        llm_executor, not directly (it makes a blocking LLM call).

        Returns None on any failure (LLM not loaded, grammar/JSON failure)
        so the caller falls back to the old regex+raw-query behavior
        exactly — this call can only improve on that baseline, never
        regress below it.
        """
        llm = self.get_llm()
        if not llm:
            return None

        attachment_note = ""
        if attached_ids:
            from app.db.models import UserDocument
            _db = SessionLocal()
            try:
                names = [
                    d.filename for d in
                    _db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
                ]
            finally:
                _db.close()
            if names:
                attachment_note = f"Attached this turn: {', '.join(names)}.\n"

        prompt = f"""{attachment_note}Message: "{message}"

Decide whether answering this message requires looking at the content of the user's uploaded document(s) in this conversation.

Output a JSON object with three keys:
- "needs_search": true only if the document's actual content is needed to answer — not for general questions, greetings, or things answerable without it.
- "whole_document": true if the user wants the document's content broadly — summarize/describe/explain it, "what is this", "what does it say", "give me a rundown/overview/walkthrough of it", "tell me everything/everything in it" — anything asking about the document as a whole rather than one specific fact. false only for a targeted lookup of one specific detail (a clause, a number, a name, a date).
- "query": if needs_search is true and whole_document is false, a short focused search phrase capturing exactly what to look up (resolve any vague wording or pronouns using the message itself) — otherwise an empty string.

Output valid JSON only. Two examples:
Targeted lookup: {{"needs_search": true, "whole_document": false, "query": "termination clause notice period"}}
Whole document: {{"needs_search": true, "whole_document": true, "query": ""}}"""

        kwargs = dict(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
        )
        grammar = _build_document_search_grammar()
        if grammar is not None:
            kwargs["grammar"] = grammar
        else:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            try:
                response = llm.create_chat_completion(**kwargs)
            except Exception as e:
                if grammar is not None:
                    logger.warning(f"Document-search grammar generation failed, retrying without it: {e}")
                    kwargs.pop("grammar", None)
                    kwargs["response_format"] = {"type": "json_object"}
                    response = llm.create_chat_completion(**kwargs)
                else:
                    raise

            content = response["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            return {
                "needs_search": bool(data.get("needs_search")),
                "whole_document": bool(data.get("whole_document")),
                "query": str(data.get("query") or "").strip(),
            }
        except Exception as e:
            logger.warning(f"Document-search decision failed: {e}")
            return None

    async def _get_document_context(
        self, message: str, attachments: Optional[List[Dict]], status_callback=None,
        precomputed_decision: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Waits for any documents attached to *this* message to finish
        ingesting, then runs RAG retrieval scoped to this conversation and
        returns a "Relevant excerpts from your uploaded documents" block
        (empty string if none), for Chat Mode's answer generation.

        Sets self._last_rag_sources as a side effect (reset to None at the
        start of every call) — the chunks actually used this turn, if any,
        for _handle_idle to persist alongside the assistant's reply.

        precomputed_decision lets a caller that already ran
        _decide_document_search itself (see app.core.workflows.engine's
        "decide_document_search" node, wired as its own visible step in a
        chat-connected workflow) skip this method's own internal call to
        it — the built-in _handle_idle path never passes this, so its
        behavior (including the has-conversation-docs gate and the
        regex-fallback path below) is completely unchanged.
        """
        self._last_rag_sources = None
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

        # Images have NO searchable content in Qdrant at all — OCR is no
        # longer used as a fallback (see documents.py), so an image is only
        # ever readable by a vision model looking at it directly via
        # _attach_vision_images. Two distinct gaps to surface explicitly
        # rather than silently answering as if nothing were attached, which
        # is indistinguishable from the model just not noticing the
        # attachment:
        #   1. Vision was active at upload (so the row looks "ready") but is
        #      no longer active by send time — content is stranded.
        #   2. No vision model was ever active — documents.py already
        #      rejected the upload outright, but the agent still needs to
        #      relay that reason instead of treating it like any other
        #      generic ingestion failure.
        vision_gap_note = ""
        if attached_ids:
            from app.db.models import UserDocument
            _db = SessionLocal()
            try:
                attached_docs = _db.query(UserDocument).filter(UserDocument.id.in_(attached_ids)).all()
            finally:
                _db.close()

            if not self.get_active_vision_mmproj_path():
                stranded = [d for d in attached_docs if d.ocr_skipped_for_vision]
                if stranded:
                    names = ", ".join(d.filename for d in stranded)
                    vision_gap_note += (
                        f"[SYSTEM NOTE]: {names} was uploaded while a vision-capable model was "
                        "active, so its content was never OCR'd or indexed — it was only ever "
                        "readable by a vision model looking at it directly. The currently active "
                        "model is no longer vision-capable, so this image's content is NOT "
                        "available right now. Tell the user to switch back to a vision-capable "
                        "model (LLM panel) and re-send to have it read, rather than answering as "
                        "if the image isn't there.\n\n"
                    )

            no_vision_failed = [
                d for d in attached_docs
                if d.status == "failed" and d.file_type in ("png", "jpg", "jpeg")
            ]
            if no_vision_failed:
                names = ", ".join(d.filename for d in no_vision_failed)
                vision_gap_note += (
                    f"[SYSTEM NOTE]: {names} could not be read — no vision-capable model was "
                    "active when it was uploaded, and this app no longer falls back to OCR for "
                    "images. Tell the user to download and activate a vision-capable model (LLM "
                    "panel) and re-upload the image so it can be read, rather than answering as "
                    "if the image isn't there.\n\n"
                )

        # LLM-driven decision: does this turn actually need document
        # search, is it a whole-document request, and if it's a targeted
        # lookup, what's the best query to search for — replaces the old
        # regex-only gate with an actual judgment call, while keeping that
        # regex as the fallback if the LLM call itself fails for any
        # reason (see _decide_document_search's docstring). Gated on the
        # conversation actually having a document at all, so a plain chat
        # where nothing was ever uploaded doesn't pay for an extra LLM call.
        import asyncio
        from app.db.models import UserDocument
        _db = SessionLocal()
        try:
            has_conversation_docs = _db.query(UserDocument.id).filter(
                UserDocument.conversation_id == self.connection_id
            ).first() is not None
        finally:
            _db.close()

        search_query = message
        if precomputed_decision is not None:
            # A "decide_document_search" node upstream already ran this
            # judgment call — reuse its result verbatim instead of paying
            # for (or duplicating the logic of) a second one.
            decision = precomputed_decision
            if not decision["needs_search"]:
                return vision_gap_note
            if decision["whole_document"] and attached_ids:
                whole_doc_context = await self._whole_document_context(attached_ids, vision_gap_note)
                if whole_doc_context is not None:
                    return whole_doc_context
            search_query = decision["query"] or message
        elif has_conversation_docs:
            loop = asyncio.get_running_loop()
            decision = await loop.run_in_executor(llm_executor, self._decide_document_search, message, attached_ids)

            if decision is not None:
                if not decision["needs_search"]:
                    return vision_gap_note
                if decision["whole_document"] and attached_ids:
                    whole_doc_context = await self._whole_document_context(attached_ids, vision_gap_note)
                    if whole_doc_context is not None:
                        return whole_doc_context
                search_query = decision["query"] or message
            elif attached_ids and self._WHOLE_DOCUMENT_INTENT_RE.search(message):
                # Fallback path: _decide_document_search failed outright —
                # reproduce the exact pre-existing behavior instead of
                # silently dropping the whole-document shortcut.
                whole_doc_context = await self._whole_document_context(attached_ids, vision_gap_note)
                if whole_doc_context is not None:
                    return whole_doc_context

        # Whole-document requests bypass retrieval entirely when the
        # content fits — see _WHOLE_DOCUMENT_INTENT_RE's comment above.
        # None means "didn't apply or didn't fit" — fall through to the
        # normal top-k retrieval below as a best-effort fallback.
        if attached_ids and self._WHOLE_DOCUMENT_INTENT_RE.search(message):
            whole_doc_context = await self._whole_document_context(attached_ids, vision_gap_note)
            if whole_doc_context is not None:
                return whole_doc_context

        if status_callback:
            await status_callback("Searching your documents...")

        from app.core import context_config as ctx_cfg
        max_rag_chunks = ctx_cfg.get("chat").get("max_rag_chunks", 5)

        try:
            loop = asyncio.get_running_loop()
            from app.core.rag.processor import hybrid_search
            relevant_chunks = await loop.run_in_executor(
                db_executor,
                lambda: hybrid_search(query=search_query, conversation_id=self.connection_id, top_k=max_rag_chunks)
            )
        except Exception as e:
            logger.warning(f"RAG search failed: {e}")
            relevant_chunks = []

        if not relevant_chunks:
            logger.info(f"RAG retrieved 0 chunks for query: {search_query}")
            # Last-resort fallback: the classifier said search was needed but
            # got whole_document wrong (observed live — a small local model
            # misjudging "give me a rundown of everything in this file" as a
            # targeted lookup, producing a weak query that scored nothing
            # above _MIN_RERANK_SCORE). Rather than surface "I don't have
            # access to that file" when a document plainly WAS attached this
            # turn, try the whole-document path once before giving up.
            if attached_ids:
                whole_doc_context = await self._whole_document_context(attached_ids, vision_gap_note)
                if whole_doc_context is not None:
                    return whole_doc_context

            # Still nothing — backfill from whatever chunks actually
            # answered the last turn or two in this conversation, ported
            # from AnythingLLM's fillSourceWindow. A vague follow-up ("what
            # about the other part?") is usually still about the same
            # document the previous answer was grounded in; leaving it with
            # zero context just because THIS turn's query didn't score well
            # on its own produces a noticeably worse answer than reusing
            # recent, still-relevant grounding.
            backfilled = await loop.run_in_executor(
                db_executor,
                lambda: self._backfill_sources_from_history(max_rag_chunks)
            )
            if backfilled:
                logger.info(f"RAG search empty — backfilled {len(backfilled)} chunks from recent turns.")
                self._last_rag_sources = backfilled
                document_context = vision_gap_note + "Relevant excerpts from your uploaded documents:\n\n"
                for chunk in backfilled:
                    document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"
                return document_context

            return vision_gap_note

        logger.info(f"RAG retrieved {len(relevant_chunks)} chunks for query: {search_query}")
        self._last_rag_sources = [
            {"id": c.get("id"), "content": c.get("content"), "filename": c.get("filename"), "document_id": c.get("document_id")}
            for c in relevant_chunks
        ]
        document_context = vision_gap_note + "Relevant excerpts from your uploaded documents:\n\n"
        for chunk in relevant_chunks:
            document_context += f"--- Source: {chunk.get('filename')} ---\n{chunk.get('content')}\n\n"
        return document_context

    def _backfill_sources_from_history(self, max_chunks: int) -> List[Dict]:
        """
        Fallback when a fresh search comes back with nothing: reuse the
        document chunks that actually answered the last turn or two in this
        conversation, rather than leaving a vague follow-up ungrounded.
        Ported from AnythingLLM's fillSourceWindow, adapted to this app's
        own per-turn source persistence (rag_sources_json) instead of their
        stored citation JSON. Blocking (plain SQLite query) — callers run
        this via db_executor, same as every other DB call in this class.
        """
        from app.db.models import ChatMessage
        _db = SessionLocal()
        try:
            recent = (
                _db.query(ChatMessage)
                .filter(
                    ChatMessage.conversation_id == self.connection_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.rag_sources_json.isnot(None),
                )
                .order_by(ChatMessage.id.desc())
                .limit(3)
                .all()
            )
            # Read rag_sources_json while the session is open — SQLAlchemy
            # attributes aren't guaranteed accessible after the session
            # that loaded them closes.
            raw_sources = [row.rag_sources_json for row in recent]
        finally:
            _db.close()

        seen_ids = set()
        backfilled: List[Dict] = []
        for raw in raw_sources:
            try:
                sources = json.loads(raw) or []
            except Exception:
                continue
            for src in sources:
                if len(backfilled) >= max_chunks:
                    return backfilled
                src_id = src.get("id")
                if src_id is not None and src_id in seen_ids:
                    continue
                seen_ids.add(src_id)
                backfilled.append(src)
        return backfilled

    async def _handle_idle(self, message: str, token_callback=None, status_callback=None, attachments: Optional[List[Dict]] = None, export_format: Optional[str] = None) -> str:
        """Chat Mode's only path now — Agent Mode's plan/confirm/execute
        branch that used to live here (selected via a `mode` parameter) has
        been removed entirely, replaced by user-designed workflows (see
        app.core.workflows.engine)."""
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

        # Kept as a block (rather than dedented) so this diff stays a clean,
        # reviewable removal — the `if mode == "agent":` branch that used to
        # follow this one is gone, along with the `mode` parameter itself.
        if True:
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

            all_tools_str = self.get_available_tools(message)
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

            sanitized_history = _trim_history_to_token_budget(test_llm, chat_prompt, sanitized_history)
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

            await self._append_history("assistant", chat_response, rag_sources=self._last_rag_sources)

            return chat_response

