"""
Aegis — Voice Transcription (faster-whisper)

Local speech-to-text for the chat composer's mic button — audio never
leaves the machine. Unlike GGUF LLM models and the scraper's Chromium
binary (which are multi-GB and downloaded on demand into AEGIS_DATA_DIR,
see app/core/scraper.py), the Whisper model is small enough (~500MB) and
universally needed enough that it ships INSIDE the app bundle itself — the
one-time download happens on the build machine (see
backend/scripts/download_whisper_model.py, run by `npm run build:python`
before pyinstaller), not on each user's machine. So every packaged build
already has it; there's no marketplace install step for this feature.

WHISPER_MODELS_DIR lives under app/ (not backend/'s top level) specifically
so PyInstaller's Analysis picks it up the same way it already does for
app/marketplace/skills — a plain __file__-relative path that resolves
correctly both in dev (venv) and inside the frozen onedir bundle. See
main.spec's `datas` for the matching bundle-time glob.

CPU-only by design, not "auto" device selection: this needs to behave
identically and safely on every machine this ships to, including ones with
no GPU or a CUDA install this was never tested against. A few seconds of
chat-length voice input transcribes in well under a second on CPU with the
"small" model at int8 — there's no real latency reason to risk a GPU path
here the way there is for a full LLM's continuous generation.
"""

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WHISPER_MODELS_DIR = Path(__file__).resolve().parent.parent / "whisper_bundled"

# "small" (~500MB) — meaningfully more accurate than tiny/base for real
# speech, still light enough for a RAM-constrained machine already running
# a local LLM. Revisit if this turns out to compete too much with the LLM
# for memory on 8GB machines.
MODEL_SIZE = "small"
COMPUTE_TYPE = "int8"

_model = None
_model_lock = threading.Lock()


def is_installed() -> bool:
    """
    True if the model files are already cached locally. Uses
    local_files_only=True specifically so this can never trigger a
    download itself — a pure status check.
    """
    try:
        from faster_whisper import WhisperModel
        WhisperModel(
            MODEL_SIZE,
            download_root=str(WHISPER_MODELS_DIR),
            device="cpu",
            compute_type=COMPUTE_TYPE,
            local_files_only=True,
        )
        return True
    except Exception:
        return False


def install() -> tuple[bool, str]:
    """
    Blocking — downloads the Whisper model into WHISPER_MODELS_DIR. Only
    ever called from backend/scripts/download_whisper_model.py at build
    time, never from a running app — end users never trigger a download.
    """
    try:
        from faster_whisper import WhisperModel
        WhisperModel(
            MODEL_SIZE,
            download_root=str(WHISPER_MODELS_DIR),
            device="cpu",
            compute_type=COMPUTE_TYPE,
        )
        return True, ""
    except Exception as e:
        logger.error(f"Failed to download Whisper model: {e}")
        return False, str(e)


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(
                    MODEL_SIZE,
                    download_root=str(WHISPER_MODELS_DIR),
                    device="cpu",
                    compute_type=COMPUTE_TYPE,
                    local_files_only=True,
                )
    return _model


def transcribe(audio_path: str) -> str:
    """
    Blocking — transcribes an audio file to text. Raises if the bundled
    model is missing (callers should check is_installed() first — see
    app/api/voice.py; a proper build always has it, so this is really just
    a guard against a broken/incomplete build rather than a normal path).
    """
    model = _get_model()
    segments, _info = model.transcribe(audio_path, beam_size=1)
    return " ".join(seg.text.strip() for seg in segments).strip()
