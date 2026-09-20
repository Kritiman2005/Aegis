"""
Aegis — media engine registry (OCR + audio/video transcription)

Mirrors app.core.extraction_engines' shape (a small per-capability list of
swappable engines, one marked default) for the workflow canvas's two Media
tool nodes (transcribe_media, extract_image_text — see
app.core.agents.chat._extraction_tool_defs). Deliberately scoped to those
two workflow nodes only: the chat composer's live mic transcription
(app.core.transcription) keeps using its own bundled default unchanged,
exactly like a Workflow's Extract node picking a non-default engine never
touches app.core.rag.processor's own extraction path.

Unlike extraction engines (every alternative already ships as an installed
pip dependency in the app's own requirements — "install" there is just an
enable/disable preference), every engine here needs a real install step —
either a genuine weight download (EasyOCR, a non-default Whisper size), or
a plain pip install whose models ship inside the wheel itself (RapidOCR —
see _install_rapidocr in app.api.marketplace_media). Handled by
app.api.marketplace_media (background task + progress broadcast, same
shape as Marketplace's embedding/reranker downloads). This module only
owns the catalog, on-disk install state, and the actual OCR/transcription
call once an engine is resolved.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_data_dir = os.environ.get("AEGIS_DATA_DIR")
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEDIA_ENGINES_DIR = Path(_data_dir) / "media_engines" if _data_dir else _BASE_DIR / "media_engines"

# Written into an engine's own directory only after its download finishes
# successfully — a partial/failed download never leaves this behind, so
# is_engine_installed can't mistake an interrupted download for a real one.
_MARKER = ".installed"

OCR_ENGINES: List[Dict[str, Any]] = [
    {
        "id": "rapidocr",
        "display_name": "RapidOCR",
        "description": (
            "Aegis's own default OCR choice — fast, ONNX-only. Not bundled "
            "(keeps the app light); installs in seconds since its models "
            "ship inside the package itself, no separate weight download."
        ),
        "default": True,
        "downloadable": True,
        "size_gb": 0.15,
    },
    {
        "id": "easyocr",
        "display_name": "EasyOCR",
        "description": (
            "Deep-learning OCR (CRAFT detector + CRNN recognizer), 80+ languages — "
            "often better than RapidOCR on handwriting, low-contrast, or rotated "
            "text. Downloads ~100MB of model weights on install."
        ),
        "default": False,
        "downloadable": True,
        "size_gb": 0.1,
    },
]

# hf_size is the faster_whisper model-size name (Systran/faster-whisper-{hf_size}
# on Hugging Face) — sizes and rough int8 CTranslate2 download sizes per
# faster-whisper's own published models.
TRANSCRIPTION_ENGINES: List[Dict[str, Any]] = [
    {
        "id": "whisper-small",
        "display_name": "faster-whisper (small)",
        "description": "Bundled — Aegis's own default, ~500MB, a solid balance of speed and accuracy.",
        "default": True,
        "downloadable": False,
    },
    {
        "id": "whisper-tiny",
        "display_name": "faster-whisper (tiny)",
        "description": "Fastest and lightest — noticeably less accurate. Good for quick drafts on slower machines.",
        "default": False,
        "downloadable": True,
        "size_gb": 0.075,
        "hf_size": "tiny",
    },
    {
        "id": "whisper-base",
        "display_name": "faster-whisper (base)",
        "description": "A step up from tiny — still fast, better accuracy.",
        "default": False,
        "downloadable": True,
        "size_gb": 0.14,
        "hf_size": "base",
    },
    {
        "id": "whisper-medium",
        "display_name": "faster-whisper (medium)",
        "description": "Noticeably more accurate than small, especially on accents or background noise — slower.",
        "default": False,
        "downloadable": True,
        "size_gb": 1.5,
        "hf_size": "medium",
    },
    {
        "id": "whisper-large-v3",
        "display_name": "faster-whisper (large-v3)",
        "description": "Most accurate, slowest — best for difficult audio (heavy accents, overlapping speech, noisy recordings).",
        "default": False,
        "downloadable": True,
        "size_gb": 3.1,
        "hf_size": "large-v3",
    },
]

_CAPABILITIES = {"ocr": OCR_ENGINES, "transcription": TRANSCRIPTION_ENGINES}


def _catalog(capability: str) -> List[Dict[str, Any]]:
    return _CAPABILITIES.get(capability, [])


def get_entry(capability: str, engine_id: str) -> Optional[Dict[str, Any]]:
    return next((e for e in _catalog(capability) if e["id"] == engine_id), None)


def _engine_path(capability: str, engine_id: str) -> Path:
    """Pure path computation, no filesystem side effect — safe to call from
    a read-only status check (is_engine_installed, polled by every GET)
    without littering MEDIA_ENGINES_DIR with an empty directory per engine
    that was merely looked at, never installed."""
    return MEDIA_ENGINES_DIR / capability / engine_id


def engine_dir(capability: str, engine_id: str) -> Path:
    """Same path, but actually creates it — for callers about to write into
    it (a download in progress, or WhisperModel's own download_root)."""
    d = _engine_path(capability, engine_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def mark_installed(capability: str, engine_id: str) -> None:
    (engine_dir(capability, engine_id) / _MARKER).touch()


def is_engine_installed(capability: str, engine_id: str) -> bool:
    """True for a catalog engine with its marker file present, or for a
    custom (Hugging-Face-search-installed) engine with its marker file
    present — the marker check alone already covers both catalog and
    custom ids identically, no separate lookup needed. Nothing in this
    catalog is "always installed" any more — even the two defaults
    (RapidOCR, faster-whisper small) need a real install step now (a pip
    install for RapidOCR, or — for faster-whisper — the app's own bundled
    weights, see app.core.transcription, which this module doesn't
    manage)."""
    return (_engine_path(capability, engine_id) / _MARKER).exists()


def resolve_engine_id(capability: str, engine_id: Optional[str]) -> str:
    """Falls back to the capability's default when engine_id is unset,
    unrecognized, or was picked but never actually finished installing —
    same fallback contract as app.core.extraction_engines.extract(). Does
    NOT check whether the default itself is installed — a caller
    (run_ocr/run_transcription) that reaches an uninstalled default just
    raises a real, actionable ModuleNotFoundError/FileNotFoundError,
    caught and humanized same as any other missing-dependency case."""
    if engine_id and is_engine_installed(capability, engine_id):
        return engine_id
    return get_default_engine_id(capability)


def get_default_engine_id(capability: str) -> str:
    entry = next((e for e in _catalog(capability) if e.get("default")), None)
    return entry["id"] if entry else ""


def list_custom_engine_ids(capability: str) -> List[str]:
    """Every installed engine under MEDIA_ENGINES_DIR/<capability> that
    ISN'T one of this capability's own catalog ids — a Hugging-Face-
    search-installed custom model (app.api.marketplace_media's
    install-custom routes)."""
    base = MEDIA_ENGINES_DIR / capability
    if not base.is_dir():
        return []
    catalog_ids = {e["id"] for e in _catalog(capability)}
    return [
        d.name for d in base.iterdir()
        if d.is_dir() and d.name not in catalog_ids and (d / _MARKER).exists()
    ]


def uninstall_engine(capability: str, engine_id: str) -> None:
    entry = get_entry(capability, engine_id)
    if not entry and engine_id not in list_custom_engine_ids(capability):
        raise ValueError(f"'{engine_id}' isn't an installed {capability} engine.")
    if entry and entry.get("default") and capability == "transcription":
        raise ValueError("The bundled default transcription model can't be removed — it's what the chat composer's mic button uses.")
    shutil.rmtree(engine_dir(capability, engine_id), ignore_errors=True)


# ── Execution ────────────────────────────────────────────────────────────
# Each lazily-loaded engine instance is reused across calls, same pattern
# as app.core.transcription._get_model — one instance per (capability,
# resolved engine_id) since a run can pick a different installed engine
# from one call to the next.

_ocr_instances: Dict[str, Any] = {}
_whisper_instances: Dict[str, Any] = {}


def run_ocr(file_path: str, engine_id: Optional[str] = None) -> str:
    resolved = resolve_engine_id("ocr", engine_id)

    if resolved == "easyocr":
        reader = _ocr_instances.get(resolved)
        if reader is None:
            import easyocr
            reader = easyocr.Reader(
                ["en"], gpu=False,
                model_storage_directory=str(engine_dir("ocr", "easyocr")),
                download_enabled=False,  # already downloaded by the Marketplace install step
            )
            _ocr_instances[resolved] = reader
        result = reader.readtext(file_path, detail=1)
        return "\n".join(r[1] for r in result) if result else ""

    if resolved != "rapidocr":
        # Not in OCR_ENGINES at all — a custom Hugging-Face-search-installed
        # model (see list_custom_engine_ids), run through transformers'
        # image-to-text pipeline. This is the one generic loader that
        # genuinely covers arbitrary HF "read text out of an image" models —
        # both plain OCR (TrOCR-style) and document-page extraction models
        # (Nougat, Donut, and similar — trained specifically on document/PDF
        # page images rather than general photos, same VisionEncoderDecoderModel
        # architecture and same image-to-text pipeline, confirmed directly
        # against facebook/nougat-small). See app.api.marketplace_media's
        # own docstring for why this is scoped to image-to-text-tagged
        # search results specifically, not every OCR-adjacent tag Hugging
        # Face has (captioning/document-QA models share overlapping tags
        # but need a different call shape entirely).
        pipe = _ocr_instances.get(resolved)
        if pipe is None:
            from transformers import pipeline
            # model_kwargs, not a top-level cache_dir kwarg — pipeline()
            # forwards unrecognized top-level kwargs into the task's own
            # runtime call parameters (ImageToTextPipeline._sanitize_parameters),
            # not into the model's from_pretrained(), confirmed directly
            # against the installed transformers version (a top-level
            # cache_dir raised "unexpected keyword argument").
            pipe = pipeline(
                "image-to-text", model=resolved, device=-1,
                model_kwargs={"cache_dir": str(engine_dir("ocr", resolved))},
            )
            _ocr_instances[resolved] = pipe
        result = pipe(file_path)
        return (result[0].get("generated_text") or "").strip() if result else ""

    engine = _ocr_instances.get(resolved)
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        _ocr_instances[resolved] = engine
    result, _ = engine(file_path)
    return "\n".join(line[1] for line in result) if result else ""


def run_transcription(file_path: str, engine_id: Optional[str] = None) -> str:
    resolved = resolve_engine_id("transcription", engine_id)

    if resolved == "whisper-small":
        # The exact same bundled model + call shape the chat composer's
        # mic button uses — app.core.transcription owns the actual model
        # instance/loading, this just delegates to it for a file instead
        # of a live audio stream.
        from app.core.transcription import transcribe
        return transcribe(file_path)

    instance = _whisper_instances.get(resolved)
    if instance is None:
        from faster_whisper import WhisperModel
        entry = get_entry("transcription", resolved)
        hf_size = (entry or {}).get("hf_size") or resolved
        model_size_or_path = hf_size if entry else resolved  # custom repo id, not in the catalog at all
        instance = WhisperModel(
            model_size_or_path, device="cpu", compute_type="int8",
            download_root=str(engine_dir("transcription", resolved)),
        )
        _whisper_instances[resolved] = instance

    segments, _ = instance.transcribe(file_path)
    return " ".join(seg.text.strip() for seg in segments)
