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
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _default(file_path: str, ext: str) -> str:
    from app.core.rag.processor import extract_text
    return extract_text(file_path, ext)


# ── PDF ──────────────────────────────────────────────────────────────────────

def _pdf_pdfplumber(file_path: str, ext: str) -> str:
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
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    if reader.is_encrypted:
        raise ValueError("This PDF is password-protected — remove the password and re-upload.")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _pdf_pdfminer(file_path: str, ext: str) -> str:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
    return pdfminer_extract_text(file_path)


def _pdf_ocr(file_path: str, ext: str) -> str:
    """Renders each page to an image via PyMuPDF (already a dependency) and
    runs it through the same bundled OCR engine as the extract_image_text
    tool (app.core.agents.chat._get_ocr_engine) — for scanned PDFs with no
    real text layer, where every other PDF engine here returns nothing."""
    import fitz
    from app.core.agents.chat import _get_ocr_engine

    doc = fitz.open(file_path)
    try:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected — remove the password and re-upload.")
        engine = _get_ocr_engine()
        parts: List[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            result, _ = engine(pix.tobytes("png"))
            if result:
                parts.append("\n".join(line[1] for line in result))
        return "\n\n".join(parts)
    finally:
        doc.close()


# ── DOCX ─────────────────────────────────────────────────────────────────────

def _docx_docx2txt(file_path: str, ext: str) -> str:
    import docx2txt
    return docx2txt.process(file_path) or ""


def _docx_mammoth(file_path: str, ext: str) -> str:
    import mammoth
    with open(file_path, "rb") as f:
        result = mammoth.extract_raw_text(f)
    return result.value


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _xlsx_pandas(file_path: str, ext: str) -> str:
    import pandas as pd
    sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    parts: List[str] = []
    for name, df in sheets.items():
        parts.append(f"# {name}")
        parts.append(df.to_string(index=False, header=False, na_rep=""))
    return "\n\n".join(parts)


# ── Registry ─────────────────────────────────────────────────────────────────

ENGINES: Dict[str, List[Dict[str, Any]]] = {
    "pdf": [
        {"id": "pymupdf", "name": "PyMuPDF", "description": "Aegis's built-in PDF extractor — fast, handles most PDFs well.", "default": True, "fn": _default},
        {"id": "pdfplumber", "name": "pdfplumber", "description": "Slower but much better at tables and layout-heavy PDFs.", "default": False, "fn": _pdf_pdfplumber},
        {"id": "pypdf", "name": "pypdf", "description": "Lightweight, pure-Python alternative — good for simple text-only PDFs.", "default": False, "fn": _pdf_pypdf},
        {"id": "pdfminer_six", "name": "pdfminer.six", "description": "Low-level raw text extraction — sometimes recovers text the others miss.", "default": False, "fn": _pdf_pdfminer},
        {"id": "ocr", "name": "OCR (scanned PDFs)", "description": "Renders each page as an image and runs OCR — for scanned PDFs with no real text layer.", "default": False, "fn": _pdf_ocr},
    ],
    "docx": [
        {"id": "python_docx", "name": "python-docx", "description": "Aegis's built-in Word extractor — preserves headings and tables.", "default": True, "fn": _default},
        {"id": "docx2txt", "name": "docx2txt", "description": "Simpler, faster plain-paragraph extraction.", "default": False, "fn": _docx_docx2txt},
        {"id": "mammoth", "name": "mammoth", "description": "Converts via its HTML pipeline — good structure preservation.", "default": False, "fn": _docx_mammoth},
    ],
    "pptx": [
        {"id": "python_pptx", "name": "python-pptx", "description": "Aegis's built-in PowerPoint extractor.", "default": True, "fn": _default},
    ],
    "xlsx": [
        {"id": "openpyxl", "name": "openpyxl", "description": "Aegis's built-in Excel extractor — cell-by-cell, exact values.", "default": True, "fn": _default},
        {"id": "pandas", "name": "pandas", "description": "Renders each sheet as a formatted table — often better for numeric/tabular data.", "default": False, "fn": _xlsx_pandas},
    ],
    "text": [
        {"id": "plain", "name": "Plain text read", "description": "Markdown/TXT/CSV — a direct read, nothing to swap.", "default": True, "fn": _default},
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


def extract(file_path: str, ext: str, engine_id: Optional[str] = None) -> str:
    """
    Runs the given engine (or the format's default when engine_id is unset
    or unrecognized) against file_path. Falls back to
    app.core.rag.processor.extract_text directly for any format with no
    registry entry (e.g. an extension not in _EXT_TO_FORMAT at all) so this
    is always at least as capable as calling that function directly.
    """
    fmt = format_for_ext(ext)
    engines = ENGINES.get(fmt or "", [])
    if not engines:
        from app.core.rag.processor import extract_text
        return extract_text(file_path, ext)

    entry = next((e for e in engines if e["id"] == engine_id), None) if engine_id else None
    if entry is None:
        entry = next(e for e in engines if e["default"])

    return entry["fn"](file_path, ext)
