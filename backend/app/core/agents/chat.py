import json
import logging
import re
from typing import Dict, List, Optional, Any
import anyio

from app.core.llm_manager import LLMManager
from app.core.friendly_errors import humanize_exception
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
    last message here — see app.core.workflows.engine's chat-generation
    node, the only live caller) is never dropped even if that alone leaves
    things tight: cutting off the user's actual question is worse than a
    rare context-limit error.

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
            # Re-raise to abort the turn and let the caller (run_chat_workflow
            # or wherever this is awaited) surface the failure instead of
            # silently losing the message.
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
        Tools that aren't MCP servers: sandboxed local-filesystem tools
        (app.core.filesystem_tools — always offered, no install step, since
        they're pure-Python stdlib and confined to the user's home
        directory by construction rather than needing a native download).

        Export-to-file isn't offered as a tool call here at all — it's
        handled by a dedicated "export_document" workflow node
        (app.core.workflows.engine's _run_export_document_node), driven by
        a classifier "llm" node's structured output rather than the model
        choosing to call a tool, so there's no plan for a small model to
        get wrong.
        """
        return self._get_filesystem_tool_defs()

    @staticmethod
    def list_local_tools_for_palette() -> List[Dict]:
        """Local tools for the workflow canvas's node palette (app.api.workflows)."""
        return ChatAgent._get_filesystem_tool_defs() + ChatAgent._extraction_tool_defs()

    @staticmethod
    def _extraction_tool_defs() -> List[Dict]:
        """
        Media tools — no install step for the tool itself (the underlying
        engine each one resolves to at run time might still need one; see
        app.core.media_engines): transcribe_media reuses the bundled
        faster-whisper model (app.core.transcription, same one the mic
        composer uses) against a file instead of a live stream by default,
        or a Workflow's own chosen alternative size; extract_image_text
        runs RapidOCR by default (install-on-demand — see
        app.core.media_engines' own docstring) or a Workflow's own chosen
        alternative engine. Always offered here, same as the filesystem
        tools — no marketplace install/status gating on the TOOL itself,
        only on which ENGINE it ends up calling.
        """
        return [
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
        each step is before they approve it.
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

    def _all_available_tools(self) -> List[Dict]:
        """
        Single source of truth for "every tool that exists" — MCP-connected
        servers' tools plus local tools (filesystem, export). EVERY
        consumer that needs the full tool list (plan
        validation, execution, prompt building, the query-rewrite pass)
        MUST call this rather than reconstructing the list by hand, so a
        newly connected/disconnected MCP server is picked up everywhere at
        once. With zero MCP servers connected, mcp_registry.list_all_tools()
        returns [] and this is identical to local-tools-only.
        """
        return mcp_registry.list_all_tools() + self._get_local_tools()


    # The only bound on how much of a read file reaches the LLM per call.
    # ~6000 chars is a rough budget-conscious default for a
    # single tool result among possibly several in one turn; the
    # offset/next_offset/has_more mechanism below is how the model reads
    # further into a longer file instead of forcing every chunk to fit at
    # once.
    _CHUNK_TEXT_CHARS = 6000

    def _chunk_text(self, full_text: str, offset: int) -> Dict:
        """
        Used by read_file — slices `full_text` into one _CHUNK_TEXT_CHARS
        window starting at `offset`, reporting whether there's more so the
        caller can walk further into a file longer than one chunk instead
        of only ever seeing the first chunk again on a re-call.
        """
        offset = max(0, offset)
        chunk = full_text[offset:offset + self._CHUNK_TEXT_CHARS]
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

    async def _execute_transcribe_media(self, arguments: Dict, engine_id: Optional[str] = None) -> Dict:
        """
        transcribe_media — bundled faster-whisper "small" by default (the
        same one the mic composer uses), or a Workflow's Media tool node's
        own chosen engine (app.core.media_engines — an extra Whisper size
        downloaded from Marketplace). engine_id is never part of the tool's
        own inputSchema/arguments — it's a node-config choice threaded in
        by app.core.workflows.engine._run_tool_node, same as an Extract
        node's per-format engine never being something the model fills in.
        """
        from app.core.media_engines import run_transcription
        from app.core.transcription import is_installed

        file_path = arguments.get("file_path", "")
        if not engine_id and not is_installed():
            return {"success": False, "text": "", "error": "Transcription model isn't available in this build."}
        try:
            text = await anyio.to_thread.run_sync(run_transcription, file_path, engine_id)
            return {"success": True, "text": text, "error": None}
        except Exception as e:
            return {"success": False, "text": "", "error": humanize_exception(e, context="transcribing that")}

    async def _execute_extract_image_text(self, arguments: Dict, engine_id: Optional[str] = None) -> Dict:
        """
        extract_image_text — bundled RapidOCR by default (install-on-
        demand — see app.core.media_engines' own docstring), or a
        Workflow's Media tool node's own chosen engine (e.g. EasyOCR,
        downloaded from Marketplace). See _execute_transcribe_media's
        docstring for why engine_id is threaded in separately rather than
        being a tool argument.
        """
        from app.core.media_engines import run_ocr

        file_path = arguments.get("file_path", "")
        try:
            text = await anyio.to_thread.run_sync(run_ocr, file_path, engine_id)
        except Exception as e:
            return {"success": False, "text": "", "error": humanize_exception(e, context="reading text from that image")}

        return {"success": bool(text), "text": text, "error": None if text else "No text detected in that image."}

    _FILESYSTEM_TOOL_NAMES = {
        "search_local_files", "list_folder", "read_file",
        "write_file", "copy_file", "move_file", "delete_file", "export_file",
    }
    # Every filesystem tool except the write/copy/move/delete ones only ever
    # looks at the disk — used for the plan-confirmation card's
    # [read-only]/[writes] badge.
    _READ_ONLY_TOOL_NAMES = {"search_local_files", "list_folder", "read_file"}

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
    _READ_ONLY_TOOL_NAMES = {"search_local_files", "list_folder", "read_file"}

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

    async def _execute_export_document(self, arguments: Dict) -> Dict:
        """
        Converts markdown to a file (app.core.exporter.export_markdown) and
        stores it under a short-lived ID (app.api.export.store_export)
        instead of returning the bytes themselves — the caller
        (app.core.workflows.engine's "export_document" node, via
        _run_export_document_node) isn't an HTTP handler, so there's no
        request/response cycle to hand raw file bytes back through. The
        returned download_url is what actually lets the user get the file:
        an absolute link to this backend's own /api/export/download/{id}
        route, rendered as a normal markdown link in the chat message.

        Not offered as an LLM-callable tool — called directly as a plain
        function from the "export_document" node's own dispatch instead.
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

    def _call_llm_text(self, messages, token_callback=None, max_tokens=None):
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
            response = llm.create_chat_completion(
                messages=msgs, temperature=0.7, stream=True, max_tokens=max_tokens
            )
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


