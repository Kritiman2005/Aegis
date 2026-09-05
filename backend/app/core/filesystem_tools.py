"""
Aegis — Sandboxed local-filesystem tools for Agent Mode

Backs the search_local_files / read_file / list_folder / write_file local
tools (app.core.agents.chat._get_local_tools) — the same plan/confirm/execute
ceremony as web_scrape and the browser_* tools, just aimed at the user's own
laptop instead of the web. Every function here is synchronous and pure
filesystem I/O; callers dispatch them off the event loop the same way
_execute_web_scrape does. (The module's own Python function is still named
search_files — only the LLM-facing tool name changed, to dodge a collision
with an unrelated Google-Drive shaper of the same name; see
_get_filesystem_tool_defs' docstring.)

Sandbox model (deliberately simple and auditable rather than OS-level):
  - Everything is confined to the user's home directory. A path outside it
    (via ".." traversal or an absolute path elsewhere) is rejected outright.
  - resolve_in_sandbox() fully resolves symlinks (Path.resolve() follows
    them) before the containment check, so directly opening/writing through
    a symlink that points outside the sandbox is refused, same as any other
    outside path. search_files/list_folder additionally never surface a
    symlink entry at all (rather than stat-ing it, which would report the
    *target's* metadata) — belt-and-suspenders against even a metadata leak
    (size/mtime of an outside file) through a planted symlink, on top of
    the content-level protection resolve_in_sandbox already gives every
    tool that actually opens a path.
  - A denylist skips known-sensitive directories (dotfiles/dotfolders,
    .ssh, .git, node_modules, Library, AppData, __pycache__, .Trash, venv)
    and credential-shaped filenames (*.pem, *.key, id_rsa*, .env*, etc.)
    everywhere under the sandbox, not just at the root.
  - Reads are capped in size (_MAX_READ_BYTES) and in returned text length
    (via ChatAgent._chunk_text, reused so read_file gets identical
    offset/next_offset/has_more pagination to web_scrape).
  - Writes never overwrite an existing file unless the caller explicitly
    asks (overwrite=True) — the default is fail-closed, not silent clobber.

None of this is a substitute for OS-level sandboxing (no seccomp/chroot/App
Sandbox entitlements) — it's a path-containment + denylist model, consistent
with how the rest of this app treats "sandboxing" (e.g. the scraper's
public-pages-only rule is enforced in Python, not by an OS jail either).
"""

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SANDBOX_ROOT = Path.home().resolve()

# Directory names skipped everywhere under the sandbox, regardless of depth.
_DENY_DIR_NAMES = {
    "Library", "AppData", "node_modules", "__pycache__", ".Trash",
    "venv", ".venv", "env", ".env",  # .env here is the dir form; the file
                                      # form (.env, .env.local, ...) is also
                                      # caught by _DENY_FILE_PATTERNS below.
}

# Credential/secret-shaped filenames, skipped wherever they appear.
_DENY_FILE_PATTERNS = (
    "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    ".env", ".env.*", "*.env",
    "credentials.json", "credentials.yml", "credentials.yaml",
    "*.kdbx",  # KeePass
)

_MAX_SEARCH_RESULTS = 100
_MAX_LIST_ENTRIES = 500
_MAX_READ_BYTES = 20 * 1024 * 1024  # 20 MB — matches a generous document size, not a media library
_MAX_WALK_ENTRIES = 200_000  # hard ceiling so a pathological tree can't hang a search forever


class SandboxError(ValueError):
    """A requested path is outside the sandbox or otherwise disallowed."""


def _is_hidden(name: str) -> bool:
    return name.startswith(".") and name not in (".", "..")


def _is_denied_dir(name: str) -> bool:
    return _is_hidden(name) or name in _DENY_DIR_NAMES


def _is_denied_file(name: str) -> bool:
    if _is_hidden(name):
        return True
    lname = name.lower()
    return any(fnmatch.fnmatch(lname, pat.lower()) for pat in _DENY_FILE_PATTERNS)


def resolve_in_sandbox(user_path: str) -> Path:
    """
    Resolves a user/model-supplied path (absolute or relative-to-home) to a
    real, symlink-resolved Path, raising SandboxError if it falls outside
    SANDBOX_ROOT or crosses a denied directory on the way there. Every
    filesystem tool below MUST route its path argument through this before
    touching disk.
    """
    raw = (user_path or "").strip()
    if not raw:
        raise SandboxError("No path was given.")

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = SANDBOX_ROOT / candidate

    try:
        resolved = candidate.resolve()
    except OSError as e:
        raise SandboxError(f"Could not resolve path: {e}")

    if resolved != SANDBOX_ROOT and SANDBOX_ROOT not in resolved.parents:
        raise SandboxError(
            f"'{user_path}' is outside the sandboxed area (your home folder) — refusing."
        )

    # Walk the relative parts and reject if any segment is a denied dir name
    # — catches e.g. Documents/.ssh/config even though .ssh itself isn't the
    # final component.
    rel_parts = resolved.relative_to(SANDBOX_ROOT).parts
    for part in rel_parts[:-1] if rel_parts else []:
        if _is_denied_dir(part):
            raise SandboxError(f"'{user_path}' is inside a restricted directory ('{part}').")

    return resolved


def _entry_meta(path: Path) -> Dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "is_dir": path.is_dir(),
        "size_bytes": None if path.is_dir() else st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def search_files(
    query: str,
    root: Optional[str] = None,
    extension: Optional[str] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None,
    min_size_kb: Optional[float] = None,
    max_size_kb: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Case-insensitive substring match on filename (query) under `root`
    (defaults to the whole sandbox), with optional extension/date/size
    filters. Denied directories are pruned from the walk entirely — their
    contents never even get stat'd, let alone returned.
    """
    if not query or not query.strip():
        return {"success": False, "error": "A search query (part of a filename) is required."}

    try:
        search_root = resolve_in_sandbox(root) if root else SANDBOX_ROOT
    except SandboxError as e:
        return {"success": False, "error": str(e)}
    if not search_root.is_dir():
        return {"success": False, "error": f"'{root or '~'}' is not a directory."}

    after_dt = _parse_date(modified_after)
    before_dt = _parse_date(modified_before)
    query_lower = query.strip().lower()
    ext_norm = extension.lower().lstrip(".") if extension else None

    matches: List[Dict[str, Any]] = []
    walked = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(search_root, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _is_denied_dir(d)]
        for fname in filenames:
            walked += 1
            if walked > _MAX_WALK_ENTRIES:
                truncated = True
                break
            if _is_denied_file(fname):
                continue
            if query_lower not in fname.lower():
                continue
            if ext_norm and not fname.lower().endswith("." + ext_norm):
                continue

            fpath = Path(dirpath) / fname
            if fpath.is_symlink():
                # A symlink can point anywhere, including outside the
                # sandbox — .stat() below follows it and would report the
                # *target's* size/mtime, silently leaking metadata about a
                # file this search was never supposed to see even without
                # exposing its content. read_file/write_file independently
                # refuse the target if actually opened (resolve_in_sandbox
                # follows symlinks before the containment check), so
                # nothing legitimate is lost by never surfacing symlinks
                # here in the first place.
                continue
            try:
                st = fpath.stat()
            except OSError:
                continue

            if after_dt and st.st_mtime < after_dt:
                continue
            if before_dt and st.st_mtime > before_dt:
                continue
            size_kb = st.st_size / 1024
            if min_size_kb is not None and size_kb < min_size_kb:
                continue
            if max_size_kb is not None and size_kb > max_size_kb:
                continue

            matches.append({
                "path": str(fpath),
                "name": fname,
                "size_bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
            if len(matches) >= _MAX_SEARCH_RESULTS:
                truncated = True
                break
        if truncated:
            break

    return {
        "success": True,
        "query": query,
        "root": str(search_root),
        "count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


def list_folder(path: Optional[str] = None) -> Dict[str, Any]:
    """Lists the immediate children of a directory (not recursive)."""
    try:
        target = resolve_in_sandbox(path) if path else SANDBOX_ROOT
    except SandboxError as e:
        return {"success": False, "error": str(e)}
    if not target.exists():
        return {"success": False, "error": f"'{path or '~'}' does not exist."}
    if not target.is_dir():
        return {"success": False, "error": f"'{path or '~'}' is not a directory."}

    entries: List[Dict[str, Any]] = []
    truncated = False
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return {"success": False, "error": f"Could not list '{path or '~'}': {e}"}

    for child in children:
        name = child.name
        if child.is_symlink():
            # Same reasoning as search_files: a symlink can point outside
            # the sandbox, and stat'ing it would report the target's own
            # metadata (size, is_dir, mtime) rather than anything about the
            # symlink itself — never surface it rather than risk that leak.
            continue
        if (child.is_dir() and _is_denied_dir(name)) or (child.is_file() and _is_denied_file(name)):
            continue
        try:
            entries.append(_entry_meta(child))
        except OSError:
            continue
        if len(entries) >= _MAX_LIST_ENTRIES:
            truncated = True
            break

    return {
        "success": True,
        "path": str(target),
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


def read_file_text(path: str) -> Dict[str, Any]:
    """
    Extracts a file's text content via the same app.core.rag.processor
    pipeline used for chat uploads (PDF/DOCX/XLSX/PPTX/CSV/plain-text/
    images-via-OCR/audio-video-via-transcription) — the caller is
    responsible for chunking/pagination (see ChatAgent._chunk_text); this
    just returns the full extracted text plus basic file metadata.
    """
    try:
        resolved = resolve_in_sandbox(path)
    except SandboxError as e:
        return {"success": False, "error": str(e)}
    if not resolved.exists():
        return {"success": False, "error": f"'{path}' does not exist."}
    if not resolved.is_file():
        return {"success": False, "error": f"'{path}' is not a file."}
    if _is_denied_file(resolved.name):
        return {"success": False, "error": f"'{path}' looks like a credential/secret file — refusing to read it."}

    size = resolved.stat().st_size
    if size > _MAX_READ_BYTES:
        return {
            "success": False,
            "error": f"'{path}' is {size / (1024*1024):.1f} MB, over the {_MAX_READ_BYTES // (1024*1024)} MB read limit.",
        }

    ext = resolved.suffix.lstrip(".").lower()
    try:
        from app.core.rag.processor import extract_text
        text = extract_text(str(resolved), ext)
    except Exception as e:
        return {"success": False, "error": f"Could not read '{path}': {e}"}

    if not text.strip():
        return {"success": False, "error": f"No readable text content found in '{path}'."}

    return {
        "success": True,
        "path": str(resolved),
        "file_type": ext,
        "size_bytes": size,
        "text": text,
    }


def write_file(path: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
    """
    Writes plain-text content to a path in the sandbox. Fails closed: an
    existing file is never clobbered unless overwrite=True is explicit.
    Parent directories are created as needed (but still inside the sandbox
    — resolve_in_sandbox already rejected anything outside it).
    """
    if content is None:
        return {"success": False, "error": "No content was given to write."}

    try:
        resolved = resolve_in_sandbox(path)
    except SandboxError as e:
        return {"success": False, "error": str(e)}
    if resolved.exists() and not overwrite:
        return {
            "success": False,
            "error": f"'{path}' already exists — pass overwrite=true if you really want to replace it.",
        }
    if resolved.exists() and resolved.is_dir():
        return {"success": False, "error": f"'{path}' is a directory, not a file."}
    if _is_denied_file(resolved.name):
        return {"success": False, "error": f"'{path}' looks like a credential/secret filename — refusing to write it."}

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"success": False, "error": f"Could not write '{path}': {e}"}

    return {
        "success": True,
        "path": str(resolved),
        "bytes_written": len(content.encode("utf-8")),
        "overwritten": resolved.exists() and overwrite,
    }


def _parse_date(value: Optional[str]) -> Optional[float]:
    """Parses an ISO-ish date string (YYYY-MM-DD or full ISO 8601) to a Unix timestamp, or None."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
