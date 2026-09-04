"""
Build-time step — downloads the faster-whisper model into
app/core/whisper_bundled/ so it ships inside the packaged app for every
user (see app/core/transcription.py's module docstring for why this isn't
a runtime/marketplace download like GGUF models or the scraper's Chromium
binary).

Run once before packaging: `npm run build:python` calls this ahead of
pyinstaller so main.spec's `datas` glob has something to bundle. Safe to
re-run — skips the download if the model is already present.

Usage: python scripts/download_whisper_model.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.transcription import is_installed, install, WHISPER_MODELS_DIR  # noqa: E402


def main() -> int:
    if is_installed():
        print(f"[download_whisper_model] Already present at {WHISPER_MODELS_DIR} — skipping.")
        return 0

    print(f"[download_whisper_model] Downloading into {WHISPER_MODELS_DIR} ...")
    ok, err = install()
    if not ok:
        print(f"[download_whisper_model] FAILED: {err}", file=sys.stderr)
        return 1

    print("[download_whisper_model] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
