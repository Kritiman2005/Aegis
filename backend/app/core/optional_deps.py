"""
Aegis — general-purpose "install any Python package" escape hatch

Backs the Dependencies panel's free-text pip install box: a user types a
package spec (e.g. "requests==2.31.0", "some-package[extra]>=1.0") and it
installs into its own directory under AEGIS_DATA_DIR/optional_deps/custom/,
without needing a whole new Aegis release for a dependency nothing in this
app anticipated. This replaced an earlier, narrower design (a curated
"ML Runtime" bundle making torch itself an opt-in download) — that was
tried and reverted after it turned into a real, multi-layered PyInstaller +
Hugging-Face-ecosystem compatibility problem (transformers' and
huggingface_hub's own internal lazy module resolution broke whenever any
part of that interdependent stack was split between "frozen into the app"
and "installed externally at runtime" — see main.spec's own note on why
torch/transformers/sentence_transformers/easyocr are all bundled directly
now instead). A simple, self-contained package installed via this panel
doesn't have that problem; only an unusually deep, Hugging-Face-style
lazy-loading trick does.

Installed via pip's own programmatic API (pip._internal.cli.main.main),
not a subprocess — a PyInstaller-frozen app's own exe isn't a
general-purpose `python -m X` entry point, so `subprocess.run([sys.executable,
"-m", "pip", ...])` wouldn't work the way it does in a normal Python
install. Calling pip's CLI entry point in-process is the standard
workaround this exact class of app uses; pip itself is bundled into the
frozen build for this purpose (see main.spec) even though this app's own
code never otherwise touches it.

ensure_on_path() must run once, early — before main.py imports anything
that could need a custom-installed package — so a package installed in an
earlier run is importable again this run, and it's also called again
immediately after every successful install so it's usable in the SAME
run too, without needing a restart.
"""

import contextlib
import io
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_data_dir = os.environ.get("AEGIS_DATA_DIR")
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
OPTIONAL_DEPS_DIR = Path(_data_dir) / "optional_deps" if _data_dir else _BASE_DIR / "optional_deps"
CUSTOM_DIR = OPTIONAL_DEPS_DIR / "custom"

# Written into a package's own directory only after pip reports success —
# a partial/failed install never leaves this behind, so list_custom can't
# mistake an interrupted install for a real one.
_MARKER = ".installed"
# The exact spec the user typed (e.g. "requests==2.31.0"), so the panel
# can show what was actually asked for — not just the slugified dir name,
# which drops the version/extras.
_SPEC_FILE = ".spec"


class MissingDependencyError(Exception):
    """Raised by require_available() when a Marketplace feature (reranker,
    a custom sentence-transformers embedding model, EasyOCR, a custom HF
    OCR model, ...) needs a pip package that isn't installed. Callers
    (app.api.marketplace_rerankers/embeddings/media) check this BEFORE
    starting a background download, so the failure is instant and
    actionable — a popup naming exactly what to install, not a wait for a
    download that was always going to fail. Logged through this module's
    own logger (category "dependency" — see log_buffer.py's mapping),
    since "you're missing a package" belongs in the Dependencies panel's
    log regardless of which Marketplace feature surfaced it."""


# A handful of packages' importable top-level module name doesn't follow
# the plain hyphens -> underscores rule every other package this app checks
# needs (sentence-transformers -> sentence_transformers, rapidocr-
# onnxruntime -> rapidocr_onnxruntime): python-pptx installs as "pptx",
# pdfminer.six installs as "pdfminer" (the ".six" is a py2/3-compat naming
# leftover, not part of the real module path — and a literal dot isn't a
# valid top-level module name for find_spec anyway).
_IMPORT_NAME_OVERRIDES = {
    "python-pptx": "pptx",
    "pdfminer.six": "pdfminer",
}


def is_available(pip_name: str) -> bool:
    """Cheap check (no import side effects) for whether a package is
    currently importable. pip_name is the PyPI/pip install name, mapped to
    its real importable module name via _IMPORT_NAME_OVERRIDES for the few
    packages where that isn't just hyphens -> underscores."""
    import importlib.util
    module_name = _IMPORT_NAME_OVERRIDES.get(pip_name, pip_name.replace("-", "_"))
    return importlib.util.find_spec(module_name) is not None


def require_available(pip_name: str, feature: str) -> None:
    """Raises MissingDependencyError (already logged) if pip_name isn't
    installed. `feature` is a short human description of what needs it —
    e.g. "The reranker" or "EasyOCR" — used verbatim in the message a user
    actually sees, so keep it a sentence-starting noun phrase."""
    if is_available(pip_name):
        return
    msg = f"{feature} needs the '{pip_name}' package, which isn't installed. Install it from the Dependencies panel first."
    logger.error(msg)
    raise MissingDependencyError(msg)


def slugify(spec: str) -> str:
    """Extracts just the base package name from a full pip spec (strips
    version constraints, extras, environment markers — same split point as
    PEP 508's own grammar for where a bare name ends) and sanitizes it into
    a safe directory name. Re-installing the same package with a different
    version spec reuses the same directory (pip's own --upgrade handles
    replacing what's there), rather than accumulating stale duplicates."""
    name = re.split(r"[\[=<>!~; ]", spec.strip(), maxsplit=1)[0]
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return safe or "package"


def _dir_for(name: str) -> Path:
    d = CUSTOM_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_custom() -> List[Dict[str, Any]]:
    if not CUSTOM_DIR.is_dir():
        return []
    out = []
    for d in sorted(CUSTOM_DIR.iterdir()):
        if not d.is_dir() or not (d / _MARKER).exists():
            continue
        spec_file = d / _SPEC_FILE
        spec = spec_file.read_text().strip() if spec_file.exists() else d.name
        out.append({"name": d.name, "spec": spec})
    return out


def _requirements_files() -> List[Path]:
    """Both of Aegis's own requirements files — root (main app deps) and
    backend/ (extraction-library extras never merged into the root file —
    see backend/requirements.txt's own history). Frozen: main.spec bundles
    both as plain data files at sys._MEIPASS root / sys._MEIPASS/backend,
    since PyInstaller has no reason to know about them otherwise (they're
    project files, not a Python package's own data)."""
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        return [meipass / "requirements.txt", meipass / "backend" / "requirements.txt"]
    return [_BASE_DIR.parent / "requirements.txt", _BASE_DIR / "requirements.txt"]


def list_bundled() -> List[Dict[str, Any]]:
    """The app's OWN declared dependencies (from requirements.txt), each
    paired with its real installed version — not a dump of everything
    importlib.metadata happens to see on sys.path, which in a dev pyenv can
    include hundreds of packages that have nothing to do with Aegis (other
    projects sharing the same interpreter). This is what actually answers
    "what does Aegis ship with", the way list_custom() answers "what did I
    add" — the Dependencies panel shows both side by side."""
    import importlib.metadata as importlib_metadata

    seen: Dict[str, Dict[str, Any]] = {}
    for req_file in _requirements_files():
        if not req_file.exists():
            continue
        for line in req_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[\[=<>!~; ]", line, maxsplit=1)[0].strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            try:
                version = importlib_metadata.version(name)
            except importlib_metadata.PackageNotFoundError:
                version = None  # e.g. pywin32-ctypes, declared Windows-only
            seen[key] = {"name": name, "version": version}

    return sorted(seen.values(), key=lambda p: p["name"].lower())


def install_custom_sync(spec: str) -> Dict[str, Any]:
    """Blocking — call off the event loop (see app.api.optional_deps'
    background task). --upgrade so re-installing an already-installed
    package with a new version spec actually replaces it instead of pip
    treating the existing files as "already satisfied" and no-op'ing."""
    spec = spec.strip()
    if not spec:
        raise ValueError("Package spec is required.")

    name = slugify(spec)
    target = _dir_for(name)

    from pip._internal.cli.main import main as pip_main

    args = ["install", "--target", str(target), "--no-warn-script-location", "--upgrade", spec]

    # pip prints its actual failure reason ("No matching distribution
    # found...", a permission error, etc.) to stdout/stderr rather than
    # returning it — captured here so the raised exception (and, via
    # app.core.friendly_errors.humanize_exception, the message an end user
    # actually sees) carries the real reason instead of just an exit code.
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            rc = pip_main(args)
    finally:
        # pip's own setup_logging() reconfigures the ROOT logger via
        # logging.config.dictConfig() on every invocation, replacing ALL of
        # root's handlers with pip's own console handlers — silently
        # evicting log_buffer's ring-buffer handler (see its own docstring)
        # for the rest of this process's life, not just for pip's own
        # output. Must run in `finally` so it's restored on a failed
        # install too, not only a successful one.
        from app.core.log_buffer import install as _reinstall_log_buffer
        _reinstall_log_buffer()

    if rc != 0:
        output = re.sub(r"\x1b\[[0-9;]*m", "", captured.getvalue())  # strip ANSI color codes
        tail = "\n".join(line for line in output.strip().splitlines()[-5:] if line.strip())
        raise RuntimeError(tail or f"pip install failed (exit code {rc}) for '{spec}'")

    (target / _SPEC_FILE).write_text(spec)
    (target / _MARKER).touch()
    # Without this, a package installed while the app is already running
    # stays unusable until the next full restart — sys.path is a
    # per-process, in-memory list, not something a later install can
    # retroactively change on its own.
    ensure_on_path()
    return {"name": name, "spec": spec}


def uninstall_custom(name: str) -> None:
    d = CUSTOM_DIR / name
    if not d.is_dir() or not (d / _MARKER).exists():
        raise ValueError(f"'{name}' isn't installed.")
    shutil.rmtree(d, ignore_errors=True)


def ensure_on_path() -> None:
    """Adds every already-installed custom package's directory to
    sys.path — makes a previous run's (or this run's own, just-finished)
    `pip install --target` output importable, exactly as if it had been
    bundled into the app itself."""
    if not CUSTOM_DIR.is_dir():
        return
    for d in CUSTOM_DIR.iterdir():
        if not d.is_dir() or not (d / _MARKER).exists():
            continue
        path = str(d)
        if path not in sys.path:
            sys.path.insert(0, path)
