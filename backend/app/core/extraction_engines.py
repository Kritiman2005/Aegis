"""
Aegis — per-format document extraction engine registry.

Every format's "default" engine calls app.core.rag.processor.extract_text
directly, unchanged — the real document-upload/RAG pipeline
(processor.ingest_document) always calls that function itself and never
goes through this module, so nothing here changes its behavior. Every
other engine below is an alternative a Workflow's Extract node can opt
into (app.core.workflows.engine._run_extract_node) — purely additive,
mirroring the shape of app.core.embeddings' model catalog (several
choices per capability, one marked as the default).

Install-on-demand, not bundled — same story as the reranker/embeddings.
pymupdf (PDF) and python-docx (DOCX) are the two exceptions: both ship
regardless, for app.core.exporter's own PDF/DOCX export (an unrelated
write-out feature), so their formats' defaults work with zero extra
install. Every other engine here — pdfplumber/pypdf/pdfminer.six (PDF),
docx2txt/mammoth (DOCX), python-pptx (PPTX's only engine, including its
own default), openpyxl/pandas (XLSX) — needs its pip package installed
first (app.core.optional_deps.require_available raises a clear,
actionable error otherwise); the Marketplace's Document Extraction
category is where a user installs one.

On top of this list, a user can also point the Extract node at a
connected MCP tool (Connectors) — the app's own way for a user to
integrate an extraction tool Aegis doesn't ship itself, without Aegis
running arbitrary third-party Python. An MCP-backed choice is addressed by
a synthetic engine_id of the form "mcp:<server_name>:<tool_name>" rather
than a real registry entry — extract() recognizes that prefix and routes
to _extract_via_mcp_tool instead of the ENGINES lookup below. See
list_mcp_candidates for how a connected server's tools are offered as
candidates in the first place.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# A candidate MCP tool's declared input schema is checked for one of these
# parameter names, in priority order, to know which argument to hand the
# local file path to — most MCP filesystem/document tools use one of these
# conventional names. See _guess_file_arg_name.
_FILE_ARG_PRIORITY = ["file_path", "filepath", "path", "file"]


def _default(file_path: str, ext: str) -> str:
    from app.core.rag.processor import extract_text
    return extract_text(file_path, ext)


# ── PDF ──────────────────────────────────────────────────────────────────────

def _pdf_pdfplumber(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("pdfplumber", "This PDF extractor")
    import pdfplumber
    parts: List[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
            for table in page.extract_tables():
                for row in table:
                    cells = [str(c) for c in row if c]
                    if cells:
                        parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def _pdf_pypdf(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("pypdf", "This PDF extractor")
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    if reader.is_encrypted:
        raise ValueError("This PDF is password-protected — remove the password and re-upload.")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _pdf_pdfminer(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("pdfminer.six", "This PDF extractor")
    from pdfminer.high_level import extract_text as pdfminer_extract_text
    return pdfminer_extract_text(file_path)


def _pdf_ocr(file_path: str, ext: str) -> str:
    """Renders each page to an image via PyMuPDF (already a dependency,
    unlike the OCR engine itself) and runs it through
    app.core.media_engines.run_ocr — the bundled RapidOCR default, or
    whatever engine a user has installed from Marketplace's Media
    Extraction category (same install-on-demand story as everything else
    in this file) — for scanned PDFs with no real text layer, where every
    other PDF engine here returns nothing."""
    import fitz
    import tempfile
    from app.core.media_engines import run_ocr

    doc = fitz.open(file_path)
    try:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected — remove the password and re-upload.")
        parts: List[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            # run_ocr takes a file path (RapidOCR/EasyOCR's own APIs both
            # expect one) — each rendered page is a real image in its own
            # right, not something already on disk, so it's written to a
            # throwaway temp file just for this one OCR call. delete=False
            # + a manual unlink in `finally`, not a `with` block — a
            # delete=True handle can't be reopened by run_ocr on Windows
            # (its own file lock blocks a second open of the same path).
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            try:
                pix.save(tmp.name)
                text = run_ocr(tmp.name)
            finally:
                os.unlink(tmp.name)
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    finally:
        doc.close()


# ── DOCX ─────────────────────────────────────────────────────────────────────

def _docx_docx2txt(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("docx2txt", "This DOCX extractor")
    import docx2txt
    return docx2txt.process(file_path) or ""


def _docx_mammoth(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("mammoth", "This DOCX extractor")
    import mammoth
    with open(file_path, "rb") as f:
        result = mammoth.extract_raw_text(f)
    return result.value


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _xlsx_pandas(file_path: str, ext: str) -> str:
    from app.core.optional_deps import require_available
    require_available("pandas", "This XLSX extractor")
    import pandas as pd
    sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    parts: List[str] = []
    for name, df in sheets.items():
        parts.append(f"# {name}")
        parts.append(df.to_string(index=False, header=False, na_rep=""))
    return "\n\n".join(parts)


# ── Registry ─────────────────────────────────────────────────────────────────

# pip_package is None for an engine that's always available (bundled
# regardless — pymupdf/python-docx, for app.core.exporter's own PDF/DOCX
# export — or stdlib-only), and for "ocr" (special-cased through
# app.core.media_engines' own install state, not a plain pip package
# check). Every other entry needs pip_package installed (see
# app.api.marketplace's is_engine_available) before it can actually run.
ENGINES: Dict[str, List[Dict[str, Any]]] = {
    "pdf": [
        {"id": "pymupdf", "name": "PyMuPDF", "description": "Fast, general-purpose PDF extraction — handles most PDFs well.", "default": True, "fn": _default, "pip_package": None},
        {"id": "pdfplumber", "name": "pdfplumber", "description": "Slower but much better at tables and layout-heavy PDFs.", "default": False, "fn": _pdf_pdfplumber, "pip_package": "pdfplumber"},
        {"id": "pypdf", "name": "pypdf", "description": "Lightweight, pure-Python alternative — good for simple text-only PDFs.", "default": False, "fn": _pdf_pypdf, "pip_package": "pypdf"},
        {"id": "pdfminer_six", "name": "pdfminer.six", "description": "Low-level raw text extraction — sometimes recovers text the others miss.", "default": False, "fn": _pdf_pdfminer, "pip_package": "pdfminer.six"},
        {"id": "ocr", "name": "OCR (scanned PDFs)", "description": "Renders each page as an image and runs OCR — for scanned PDFs with no real text layer.", "default": False, "fn": _pdf_ocr, "pip_package": None},
    ],
    "docx": [
        {"id": "python_docx", "name": "python-docx", "description": "Preserves headings and tables when extracting Word documents.", "default": True, "fn": _default, "pip_package": None},
        {"id": "docx2txt", "name": "docx2txt", "description": "Simpler, faster plain-paragraph extraction.", "default": False, "fn": _docx_docx2txt, "pip_package": "docx2txt"},
        {"id": "mammoth", "name": "mammoth", "description": "Converts via its HTML pipeline — good structure preservation.", "default": False, "fn": _docx_mammoth, "pip_package": "mammoth"},
    ],
    "pptx": [
        {"id": "python_pptx", "name": "python-pptx", "description": "Extracts text from PowerPoint slide decks.", "default": True, "fn": _default, "pip_package": "python-pptx"},
    ],
    "xlsx": [
        {"id": "openpyxl", "name": "openpyxl", "description": "Cell-by-cell exact values from Excel spreadsheets.", "default": True, "fn": _default, "pip_package": "openpyxl"},
        {"id": "pandas", "name": "pandas", "description": "Renders each sheet as a formatted table — often better for numeric/tabular data.", "default": False, "fn": _xlsx_pandas, "pip_package": "pandas"},
    ],
    "text": [
        {"id": "plain", "name": "Plain text read", "description": "Markdown/TXT/CSV — a direct read, nothing to swap.", "default": True, "fn": _default, "pip_package": None},
    ],
}

_EXT_TO_FORMAT = {
    "pdf": "pdf", "docx": "docx", "doc": "docx", "pptx": "pptx", "ppt": "pptx",
    "xlsx": "xlsx", "md": "text", "txt": "text", "csv": "text",
}


def format_for_ext(ext: str) -> Optional[str]:
    return _EXT_TO_FORMAT.get(ext.lower())


def list_engines(fmt: str) -> List[Dict[str, Any]]:
    """Engine metadata only (no callables) — what the Marketplace/UI needs."""
    return [{k: v for k, v in e.items() if k != "fn"} for e in ENGINES.get(fmt, [])]


def _load_enabled_map() -> Dict[str, List[str]]:
    import json
    from app.db.database import SessionLocal
    from app.db.crud import get_system_settings
    with SessionLocal() as db:
        settings = get_system_settings(db)
        try:
            return json.loads(settings.extraction_engines_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}


def is_engine_enabled(fmt: str, engine_id: str) -> bool:
    """A format's own default engine is always enabled (see ENGINES'
    "default" flag) and never stored in extraction_engines_json — every
    other engine needs an explicit opt-in row there. Package availability
    (app.core.optional_deps.is_available) is a SEPARATE concern from this
    preference: an engine can be "enabled" here (the user wants it) while
    its pip package still isn't installed yet — see app.api.marketplace's
    is_engine_available, which checks both."""
    entry = next((e for e in ENGINES.get(fmt, []) if e["id"] == engine_id), None)
    if entry and entry.get("default"):
        return True
    return engine_id in _load_enabled_map().get(fmt, [])


def set_engine_enabled(fmt: str, engine_id: str, enabled: bool) -> None:
    """Toggles a non-default engine's opt-in preference — see
    is_engine_enabled's own docstring. A format's own default engine is
    always enabled and can't be toggled off (nothing calls this for one;
    see app.api.marketplace's install/uninstall routes, which never treat
    a default entry as installable/removable in the first place)."""
    import json
    from app.db.database import SessionLocal
    from app.db.crud import get_system_settings

    with SessionLocal() as db:
        settings = get_system_settings(db)
        current = _load_enabled_map()
        engines_for_fmt = set(current.get(fmt, []))
        if enabled:
            engines_for_fmt.add(engine_id)
        else:
            engines_for_fmt.discard(engine_id)
        current[fmt] = sorted(engines_for_fmt)
        settings.extraction_engines_json = json.dumps(current)
        db.commit()


def _guess_file_arg_name(tool_def: Optional[Dict[str, Any]]) -> Optional[str]:
    """Which of a candidate MCP tool's declared parameters should get the
    local file path — checked against _FILE_ARG_PRIORITY's conventional
    names first; if none match but the tool declares exactly one string
    parameter, that's an unambiguous single choice worth using too. Returns
    None (not installable as an extractor) when neither applies, or when
    the tool has more than one required parameter — Aegis can only supply
    the file path, so a tool needing a second required argument (write_file's
    "content", move_file's "destination", ...) can never actually be called
    successfully and shouldn't clutter the picker. This is still just a
    naming/shape heuristic, not proof the tool actually extracts text —
    the tool's own name and description (shown next to it in the picker)
    are what a user judges fitness from, same as picking between the
    bundled PDF engines."""
    if not tool_def:
        return None
    schema = tool_def.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    if len(required) > 1:
        return None
    for name in _FILE_ARG_PRIORITY:
        if name in props:
            return name
    string_props = [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "string"]
    return string_props[0] if len(string_props) == 1 else None


def list_mcp_candidates() -> List[Dict[str, Any]]:
    """
    Every currently-connected MCP tool (app.mcp.registry.mcp_registry) that
    plausibly accepts a file path — offered as an Extract-node engine
    choice alongside the bundled ones above, addressed by the synthetic
    "mcp:<server>:<tool>" engine_id extract() recognizes. Not scoped to one
    format: Aegis has no way to know which file types a given MCP tool
    actually handles, so every format's picker gets the same candidate list
    and the user judges fit from the tool's own name/description, same as
    picking between the bundled PDF engines by reading what each one says
    it's good at.
    """
    from app.mcp.registry import mcp_registry

    candidates = []
    for tool in mcp_registry.list_all_tools():
        name = tool.get("name")
        if not name or not _guess_file_arg_name(tool):
            continue
        server = mcp_registry.get_server_for_tool(name)
        if not server:
            continue
        candidates.append({
            "engine_id": f"mcp:{server}:{name}",
            "name": f"{server} → {name}",
            "description": tool.get("description") or "Custom extractor connected via Connectors.",
            "server": server,
            "tool": name,
        })
    return candidates


def _extract_via_mcp_tool(file_path: str, server_name: str, tool_name: str) -> str:
    from app.mcp.registry import mcp_registry

    if mcp_registry.get_server_for_tool(tool_name) != server_name:
        raise ValueError(
            f"MCP tool '{tool_name}' from server '{server_name}' isn't connected right now — "
            f"reconnect it from Connectors and try again."
        )

    tool_def = next((t for t in mcp_registry.list_all_tools() if t.get("name") == tool_name), None)
    arg_name = _guess_file_arg_name(tool_def)
    if not arg_name:
        raise ValueError(f"MCP tool '{tool_name}' doesn't declare a file-path parameter Aegis recognizes.")

    return mcp_registry.call_tool(tool_name, {arg_name: file_path})


def extract(file_path: str, ext: str, engine_id: Optional[str] = None) -> str:
    """
    Runs the given engine (or the format's default when engine_id is unset
    or unrecognized) against file_path. Falls back to
    app.core.rag.processor.extract_text directly for any format with no
    registry entry (e.g. an extension not in _EXT_TO_FORMAT at all) so this
    is always at least as capable as calling that function directly.

    engine_id prefixed "mcp:" (see list_mcp_candidates) routes to a
    connected MCP tool instead of anything in ENGINES below — Aegis's
    integration point for an extraction tool it doesn't bundle itself.
    """
    if engine_id and engine_id.startswith("mcp:"):
        _, server_name, tool_name = engine_id.split(":", 2)
        return _extract_via_mcp_tool(file_path, server_name, tool_name)

    fmt = format_for_ext(ext)
    engines = ENGINES.get(fmt or "", [])
    if not engines:
        from app.core.rag.processor import extract_text
        return extract_text(file_path, ext)

    entry = next((e for e in engines if e["id"] == engine_id), None) if engine_id else None
    if entry is None:
        entry = next(e for e in engines if e["default"])

    return entry["fn"](file_path, ext)
