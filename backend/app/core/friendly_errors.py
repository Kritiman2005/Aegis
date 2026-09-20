"""
Aegis — plain-language error translation

Three different surfaces (the Dependencies panel's log viewer, a workflow
run's error_message, and chat's "Backend error" system message) all used to
show `str(exception)` directly — raw Python exception text, sometimes a full
traceback line, meant for a developer reading logs, not for the person using
the app. humanize_exception() is the one shared translation point for all
three: pattern-match the common, actually-recurring cases in this app
(network failures, pip install failures, expired OAuth tokens, out-of-memory
model loads, missing files) into a plain sentence, and fall back to a
trimmed, traceback-stripped version of the original message rather than
inventing an explanation for something unrecognized.
"""

import re


def humanize_exception(exc: BaseException, context: str = "") -> str:
    """Returns a plain-language sentence for an end user. `context` is a
    short present-participle phrase for the fallback case, e.g. "installing
    'requests'" or "generating a response" — used as "Something went wrong
    while <context>: ...", so even the fallback reads like a sentence
    about what the user was doing, not a bare stack trace."""
    text = str(exc)
    low = text.lower()
    type_name = type(exc).__name__

    # Network / connectivity
    if any(s in low for s in ("connecterror", "connection refused", "connectionerror", "failed to establish a new connection", "network is unreachable")):
        return "Couldn't reach the network. Check your internet connection and try again."
    if any(s in low for s in ("timeout", "timed out")):
        return "The request took too long and timed out. This can happen with a slow connection or an overloaded server — try again."
    if "certificate" in low or "ssl" in low:
        return "A secure connection couldn't be established (certificate error). Check your system clock and network, then try again."

    # pip install (Dependencies panel)
    if "no matching distribution found" in low or "could not find a version that satisfies the requirement" in low:
        m = re.search(r"for ([A-Za-z0-9._-]+)", text)
        pkg = m.group(1) if m else "that package"
        return f"Couldn't find '{pkg}' on PyPI. Check the name and version are spelled correctly."
    if "could not install packages due to an oserror" in low or "permission denied" in low:
        return "Installation failed — Aegis didn't have permission to write the files it needed."

    # Missing optional package — real since several heavy ML packages
    # (torch, sentence-transformers, easyocr, rapidocr-onnxruntime) are no
    # longer bundled by default (see backend/main.spec's own note): a
    # feature that needs one of them raises a plain ModuleNotFoundError the
    # first time it's actually used, not at app startup. Actionable instead
    # of a bare "No module named X" — the Dependencies panel is exactly
    # where a user fixes this, in one step, no restart required.
    if type_name in ("ModuleNotFoundError", "ImportError"):
        m = re.search(r"no module named '?([A-Za-z0-9_.\-]+)'?", low)
        pkg = m.group(1).split(".")[0] if m else None
        if pkg:
            return f"This feature needs the '{pkg}' package, which isn't installed. Go to Dependencies and install it, then try again."

    # OAuth / connectors
    if "invalid_client" in low or "invalid_grant" in low:
        return "This connection has expired or was revoked. Reconnect it from the Connectors panel."

    # Model loading / memory
    if "out of memory" in low or "cannot allocate memory" in low or "ggml_new_object" in low:
        return "Your Mac ran out of memory while loading the model. Try closing other apps or choosing a smaller/more-quantized model."
    if "failed to load model" in low or "error loading model" in low:
        return "The AI model failed to load. Try re-downloading it from the Model Hub — the file may be incomplete or corrupted."
    if type_name == "FileNotFoundError" or "no such file or directory" in low:
        return "A file Aegis needed is missing on disk. Try re-downloading it."

    # Generic fallback — strip anything file-path/traceback-shaped, keep it short.
    cleaned = re.sub(r'File "[^"]+", line \d+.*', "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > 160:
        cleaned = cleaned[:157] + "..."
    if not cleaned:
        cleaned = type_name
    prefix = f"Something went wrong while {context}: " if context else "Something went wrong: "
    return prefix + cleaned
