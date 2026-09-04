"""
Aegis — Voice Transcription API

Exposes app.core.transcription (faster-whisper) as a small set of endpoints:
check whether the bundled model is present, then transcribe a recorded clip
from the chat composer's mic button. The model itself ships inside the app
bundle (see app/core/transcription.py's module docstring) — there's no
install step here, /status exists only so the frontend can hide the mic
button on the rare broken/incomplete build where the bundle step didn't run.

Transcription itself is a plain request/response (not a background task +
websocket broadcast like document upload/scraping) — the composer is waiting
synchronously to drop the resulting text into the textarea, so there's
nothing to gain from making it fire-and-forget.
"""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
import anyio

from app.core.transcription import transcribe, is_installed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["Voice"])


@router.get("/status")
def get_status():
    return {"status": "ready" if is_installed() else "unavailable"}


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    if not is_installed():
        raise HTTPException(status_code=409, detail="Voice model unavailable in this build.")

    # Suffix matters: faster-whisper (via av/ffmpeg) picks its demuxer off the
    # file extension, and the composer always sends browser-recorded webm/opus.
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        text = await anyio.to_thread.run_sync(transcribe, tmp_path)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail="Transcription failed.")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"text": text}
