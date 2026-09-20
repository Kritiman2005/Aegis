"""
Aegis — Embedding Model Catalog

fastembed.TextEmbedding.list_supported_models() carries ~30 models — real
choice, but too many to browse meaningfully in one flat Marketplace list.
list_catalog() below narrows that down to a small curated set spanning the
tradeoffs that actually matter (size/speed vs. quality, English vs.
multilingual) instead of dumping the whole thing. Anything else fastembed
supports is still fully reachable and downloads correctly: the Marketplace
UI's HF search box (marketplace_embeddings.py's /search + /download) can
find it by name, and get_catalog_entry below always checks the FULL
fastembed list — not just the curated one — so a model found that way still
routes through the real fastembed (ONNX) backend instead of misrouting to
the slower sentence-transformers fallback meant for genuinely non-fastembed
repos.
"""

from functools import lru_cache
from typing import Any, Dict, List, Optional

# Same "which declared parameter gets the text" heuristic as
# app.core.extraction_engines' _guess_file_arg_name and
# app.core.chunking_engines' _guess_text_arg_name — see _guess_text_arg_name below.
_TEXT_ARG_PRIORITY = ["text", "input", "content", "query"]

# Deliberately small. Each one earns its spot for a distinct reason — this
# isn't "the best 6", it's "6 that cover different real tradeoffs":
_CURATED_MODEL_IDS = {
    "BAAI/bge-base-en-v1.5",        # Aegis's own bundled default (English, balanced)
    "BAAI/bge-small-en-v1.5",       # fastest/smallest — low-RAM machines, large corpora
    "BAAI/bge-large-en-v1.5",       # highest-quality English option, most RAM/disk
    "mixedbread-ai/mxbai-embed-large-v1",  # strong modern general-purpose alternative
    "nomic-ai/nomic-embed-text-v1.5",      # long-context alternative
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",  # multilingual
}


@lru_cache(maxsize=1)
def _full_catalog() -> List[Dict[str, Any]]:
    from fastembed import TextEmbedding

    return [
        {
            "id": m["model"],
            "display_name": m["model"],
            "dim": m["dim"],
            "size_gb": round(m["size_in_GB"], 2),
            "description": m["description"],
        }
        for m in TextEmbedding.list_supported_models()
    ]


def list_catalog() -> List[Dict[str, Any]]:
    """The curated subset Marketplace actually browses — see
    _CURATED_MODEL_IDS. Not the full fastembed list; use the HF search box
    for anything else fastembed supports."""
    return [m for m in _full_catalog() if m["id"] in _CURATED_MODEL_IDS]


def get_catalog_entry(model_id: str) -> Optional[Dict[str, Any]]:
    """Always checks the FULL fastembed catalog, not just the curated
    browse list — see this module's docstring for why."""
    return next((m for m in _full_catalog() if m["id"] == model_id), None)


def _guess_text_arg_name(tool_def: Optional[Dict[str, Any]]) -> Optional[str]:
    """Which of a candidate MCP tool's declared parameters should get the
    text to embed — checked against _TEXT_ARG_PRIORITY's conventional
    names first, then a tool with exactly one required string parameter.
    None means this tool isn't offered as an embedding candidate."""
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
    that plausibly accepts a text argument — offered as an Embedding-node
    model choice alongside Marketplace-installed local models, addressed
    by the synthetic "mcp:<server>:<tool>" model id
    app.core.embeddings.manager.get_embedder recognizes. `dim` is unknown
    ahead of time (Aegis never sees the vector until the tool is actually
    called) so it's reported as 0 rather than guessed. Shaped like the
    frontend's EmbeddingModelDef (id/model_id/display_name/dim/status) so
    these can be appended straight into the same picker list."""
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
            "id": 0,
            "model_id": f"mcp:{server}:{name}",
            "display_name": f"{server} → {name}",
            "dim": 0,
            "status": "downloaded",
            "description": tool.get("description") or "Custom embedder connected via Connectors.",
            "server": server,
            "tool": name,
        })
    return candidates
