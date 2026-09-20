"""
Aegis — chunking strategy registry.

app.core.rag.processor.chunk_text is a single, deliberately-tuned
recursive strategy (paragraph -> sentence -> word, heading-aware) used
unchanged everywhere in the app's own document-upload/RAG pipeline — this
module never touches that default path. It exists purely to give a
Workflow's Chunk node a choice of alternative strategies
(app.core.workflows.engine._run_chunk_node), the same "several choices,
one marked default" shape app.core.extraction_engines already uses for
per-format extractors.

Every strategy shares processor.py's own building blocks (_pack_units,
_split_sentences, _hard_split_words, _enforce_embed_token_limit) rather
than reimplementing packing/overlap/token-limit logic a second time, and
every strategy respects a document's own section-heading boundaries
(processor._HEADING_MARKER, set by the extractors — PDF via font size,
DOCX via paragraph style, MD via '#') by never merging text across one:
a chunk never silently straddles two different sections, whichever
strategy produced it.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# A candidate MCP tool's declared input schema is checked for one of these
# parameter names, in priority order, to know which argument to hand the
# text to — see _guess_text_arg_name. Same idea as
# app.core.extraction_engines' own _guess_file_arg_name, for a text
# argument instead of a file path.
_TEXT_ARG_PRIORITY = ["text", "content", "document", "input"]


def _paragraphs_by_heading(text: str):
    """Splits text into (heading, paragraphs) groups — one group per
    section, in document order, `heading` is None for any text before the
    first heading (or when the document has none at all). Shared by every
    strategy below except "fixed" (which deliberately ignores structure
    entirely)."""
    from app.core.rag.processor import _HEADING_MARKER

    groups: List[tuple] = []
    current_heading: Optional[str] = None
    current: List[str] = []
    for line in text.split("\n"):
        para = line.strip()
        if not para:
            continue
        if para.startswith(_HEADING_MARKER):
            if current:
                groups.append((current_heading, current))
            current_heading = para[len(_HEADING_MARKER):].strip()
            current = []
            continue
        current.append(para)
    if current:
        groups.append((current_heading, current))
    return groups


def _recursive(text: str, chunk_size: int, overlap: int) -> List[str]:
    from app.core.rag.processor import chunk_text
    return chunk_text(text, chunk_size=chunk_size, overlap=overlap)


def _fixed(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Naive fixed-size word-count windows across the WHOLE text, ignoring
    paragraph/sentence/heading boundaries entirely — the fastest, most
    predictable strategy (every chunk but the last is exactly chunk_size
    words), at the cost of freely cutting mid-sentence. Good for already-
    clean, unstructured, or very short text where boundary-awareness adds
    nothing."""
    from app.core.rag.processor import _hard_split_words, _enforce_embed_token_limit
    return _enforce_embed_token_limit(_hard_split_words(text, chunk_size, overlap))


def _sentence(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Every sentence is its own atomic unit, packed up to chunk_size
    words per chunk (never split mid-sentence) — unlike "recursive", a
    chunk here is never a whole unsplit paragraph even when it would fit,
    so sentence-level boundaries are always respected. Good for dense
    prose (articles, transcripts) where sentence-level meaning matters
    more than paragraph structure."""
    from app.core.rag.processor import _split_sentences, _pack_units, _prefix_heading, _enforce_embed_token_limit

    chunks: List[str] = []
    for heading, paragraphs in _paragraphs_by_heading(text):
        sentences = [s for p in paragraphs for s in _split_sentences(p)]
        if not sentences:
            continue
        packed = _pack_units(sentences, chunk_size, overlap, joiner=" ")
        chunks.extend(_prefix_heading(packed, heading))
    return _enforce_embed_token_limit(chunks)


def _paragraph(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Every paragraph is its own atomic unit, packed up to chunk_size
    words per chunk — unlike "recursive", an oversized single paragraph is
    never broken down into sentences, it's hard-cut by word count instead
    (simpler, coarser). Good for already-structured documents (specs,
    contracts, docs with short paragraphs) where paragraph boundaries
    carry real meaning and a paragraph rarely needs further splitting."""
    from app.core.rag.processor import _pack_units, _hard_split_words, _prefix_heading, _enforce_embed_token_limit

    chunks: List[str] = []
    for heading, paragraphs in _paragraphs_by_heading(text):
        pending: List[str] = []
        for para in paragraphs:
            if len(para.split()) > chunk_size:
                if pending:
                    chunks.extend(_prefix_heading(_pack_units(pending, chunk_size, overlap, joiner="\n"), heading))
                    pending = []
                chunks.extend(_prefix_heading(_hard_split_words(para, chunk_size, overlap), heading))
                continue
            pending.append(para)
        if pending:
            chunks.extend(_prefix_heading(_pack_units(pending, chunk_size, overlap, joiner="\n"), heading))
    return _enforce_embed_token_limit(chunks)


STRATEGIES: List[Dict[str, Any]] = [
    {
        "id": "recursive", "name": "Recursive", "default": True, "fn": _recursive,
        "description": "Packs paragraphs together, falling back to sentences then words only for an oversized paragraph. The best general-purpose default — same strategy the app's own document uploads use.",
    },
    {
        "id": "paragraph", "name": "Paragraph", "default": False, "fn": _paragraph,
        "description": "Keeps each paragraph together as one unit, packed up to the chunk size. Good for structured documents (specs, contracts) where paragraph boundaries carry real meaning.",
    },
    {
        "id": "sentence", "name": "Sentence", "default": False, "fn": _sentence,
        "description": "Keeps each sentence together as one unit, packed up to the chunk size — never merges a whole paragraph into one chunk. Good for dense prose like articles or transcripts.",
    },
    {
        "id": "fixed", "name": "Fixed size", "default": False, "fn": _fixed,
        "description": "Naive fixed-size word windows across the whole text, ignoring paragraph/sentence boundaries entirely. Fastest and most predictable chunk sizes, at the cost of cutting mid-sentence.",
    },
]


def list_strategies() -> List[Dict[str, Any]]:
    """Strategy metadata only (no callables) — what the Marketplace/canvas UI needs."""
    return [{k: v for k, v in s.items() if k != "fn"} for s in STRATEGIES]


def _guess_text_arg_name(tool_def: Optional[Dict[str, Any]]) -> Optional[str]:
    """Which of a candidate MCP tool's declared parameters should get the
    text to chunk — same shape as app.core.extraction_engines'
    _guess_file_arg_name: checked against _TEXT_ARG_PRIORITY's
    conventional names first, then a tool with exactly one required string
    parameter (an unambiguous single choice). None means this tool isn't
    offered as a chunking candidate."""
    if not tool_def:
        return None
    schema = tool_def.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    if len(required) > 1:
        return None
    for name in _TEXT_ARG_PRIORITY:
        if name in props:
            return name
    string_props = [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "string"]
    return string_props[0] if len(string_props) == 1 else None


def list_mcp_candidates() -> List[Dict[str, Any]]:
    """Every currently-connected MCP tool (app.mcp.registry.mcp_registry)
    that plausibly accepts a text argument — offered as a Chunk-node
    strategy choice alongside the built-in ones above, addressed by the
    synthetic "mcp:<server>:<tool>" strategy_id chunk() recognizes. Shaped
    identically to a built-in STRATEGIES entry (id/name/description/
    default) so the frontend can append these straight into the same list
    with no special-casing."""
    from app.mcp.registry import mcp_registry

    candidates = []
    for tool in mcp_registry.list_all_tools():
        name = tool.get("name")
        if not name or not _guess_text_arg_name(tool):
            continue
        server = mcp_registry.get_server_for_tool(name)
        if not server:
            continue
        candidates.append({
            "id": f"mcp:{server}:{name}",
            "name": f"{server} → {name}",
            "description": tool.get("description") or "Custom chunker connected via Connectors.",
            "default": False,
            "server": server,
            "tool": name,
        })
    return candidates


def _parse_chunks(raw: Any) -> List[str]:
    """Best-effort unwrap of an MCP chunking tool's response into a list of
    chunk strings: a bare JSON array of strings is used directly; a JSON
    object with a "chunks"/"result"/"segments" list is unwrapped; anything
    else (plain text, or JSON matching neither shape) is split on blank
    lines — the same paragraph boundary the app's own chunker treats as
    meaningful."""
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, dict):
        for key in ("chunks", "result", "segments"):
            value = raw.get(key)
            if isinstance(value, list):
                return [str(x) for x in value if str(x).strip()]
        raw = json.dumps(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
        if isinstance(parsed, dict):
            for key in ("chunks", "result", "segments"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [str(x) for x in value if str(x).strip()]
        return [p.strip() for p in raw.split("\n\n") if p.strip()]
    return [str(raw)] if raw else []


def _chunk_via_mcp_tool(text: str, server_name: str, tool_name: str, chunk_size: int, overlap: int) -> List[str]:
    from app.mcp.registry import mcp_registry

    if mcp_registry.get_server_for_tool(tool_name) != server_name:
        raise ValueError(
            f"MCP tool '{tool_name}' from server '{server_name}' isn't connected right now — "
            f"reconnect it from Connectors and try again."
        )

    tool_def = next((t for t in mcp_registry.list_all_tools() if t.get("name") == tool_name), None)
    arg_name = _guess_text_arg_name(tool_def)
    if not arg_name:
        raise ValueError(f"MCP tool '{tool_name}' doesn't declare a text parameter Aegis recognizes.")

    args: Dict[str, Any] = {arg_name: text}
    props = (tool_def.get("inputSchema") or {}).get("properties") or {}
    if "chunk_size" in props:
        args["chunk_size"] = chunk_size
    if "overlap" in props:
        args["overlap"] = overlap

    return _parse_chunks(mcp_registry.call_tool(tool_name, args))


def chunk(text: str, chunk_size: int = 300, overlap: int = 50, strategy_id: Optional[str] = None) -> List[str]:
    """Runs the given strategy (or the default "recursive" one when
    strategy_id is unset or unrecognized) against text.

    strategy_id prefixed "mcp:" (see list_mcp_candidates) routes to a
    connected MCP tool instead of anything in STRATEGIES above — Aegis's
    integration point for a chunking approach it doesn't bundle itself."""
    if strategy_id and strategy_id.startswith("mcp:"):
        _, server_name, tool_name = strategy_id.split(":", 2)
        return _chunk_via_mcp_tool(text, server_name, tool_name, chunk_size, overlap)

    entry = next((s for s in STRATEGIES if s["id"] == strategy_id), None) if strategy_id else None
    if entry is None:
        entry = next(s for s in STRATEGIES if s["default"])
    return entry["fn"](text, chunk_size, overlap)
