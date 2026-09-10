"""
Aegis — MCP Runtime Manager

Most MCP servers are launched via `npx <package>` (Node.js) or `uvx <package>`
(Python, via astral's `uv`) rather than a raw already-installed binary. Requiring
the user to have Node or uv installed themselves — what AnythingLLM's own docs
explicitly punt on — is exactly the gap this closes: given a server's `command`,
this resolves it to something actually runnable, downloading a portable runtime
into AEGIS_DATA_DIR/runtimes on demand (once, cached after that) if the command
isn't already on the user's PATH.

Deliberately narrow: only `npx`/`node`/`npm` (→ Node.js) and `uvx`/`uv` (→ uv,
which bootstraps its own Python) are auto-installable — these two cover the
overwhelming majority of real-world MCP servers. Anything else (a direct binary
path, `python3`, `deno`, ...) either already resolves via the system PATH or is
reported back as a clear "not found, and Aegis doesn't know how to install this
automatically" error — never a silent failure, matching this app's existing
"honest about what it can't do" pattern (see e.g. app.core.scraper's module
docstring on the same principle for a different feature).
"""

import logging
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

import certifi
import httpx

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]

_data_dir = os.environ.get("AEGIS_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
RUNTIMES_DIR = Path(_data_dir) / "runtimes" if _data_dir else BASE_DIR / "runtimes"

_HTTP_TIMEOUT = httpx.Timeout(60.0, read=None)


async def _noop_progress(**kwargs) -> None:
    pass


# ─── Platform detection ─────────────────────────────────────────────────────

def _system_arch() -> tuple[str, str]:
    system = platform.system()  # 'Darwin' | 'Linux' | 'Windows'
    machine = platform.machine().lower()  # 'arm64' | 'x86_64' | 'amd64' | 'aarch64' | ...
    if machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("amd64", "x86_64"):
        arch = "x64"
    else:
        arch = machine  # unsupported — surfaced as a download failure downstream
    return system, arch


def _node_asset_name(version: str) -> tuple[str, str]:
    """Returns (filename, archive_type) for the current platform. version includes the leading 'v'."""
    system, arch = _system_arch()
    if system == "Darwin":
        plat = f"darwin-{arch}"
        ext = "tar.gz"
    elif system == "Linux":
        plat = f"linux-{arch}"
        ext = "tar.gz"
    elif system == "Windows":
        plat = f"win-{arch}"
        ext = "zip"
    else:
        raise RuntimeError(f"Unsupported platform for Node.js auto-install: {system}")
    return f"node-{version}-{plat}.{ext}", ext


def _uv_target(version: str) -> tuple[str, str]:
    """Returns (filename, archive_type) for the current platform. version has no leading 'v'."""
    system, arch = _system_arch()
    triple_arch = "aarch64" if arch == "arm64" else "x86_64"
    if system == "Darwin":
        target = f"{triple_arch}-apple-darwin"
        ext = "tar.gz"
    elif system == "Linux":
        target = f"{triple_arch}-unknown-linux-gnu"
        ext = "tar.gz"
    elif system == "Windows":
        target = f"{triple_arch}-pc-windows-msvc"
        ext = "zip"
    else:
        raise RuntimeError(f"Unsupported platform for uv auto-install: {system}")
    return f"uv-{target}.{ext}", ext


# ─── Version resolution ─────────────────────────────────────────────────────

async def _latest_node_lts_version() -> str:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=certifi.where()) as client:
        resp = await client.get("https://nodejs.org/dist/index.json")
        resp.raise_for_status()
        releases = resp.json()
    for release in releases:
        if release.get("lts"):
            return release["version"]  # e.g. "v24.20.0"
    raise RuntimeError("Could not determine the latest Node.js LTS version.")


async def _latest_uv_version() -> str:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=certifi.where()) as client:
        resp = await client.get("https://api.github.com/repos/astral-sh/uv/releases/latest")
        resp.raise_for_status()
        return resp.json()["tag_name"]  # e.g. "0.12.10"


# ─── Download + extract ─────────────────────────────────────────────────────

async def _download(url: str, dest: Path, progress_cb: ProgressCallback, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=certifi.where()) as client:
        async with client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0))
            last_reported = -1
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192 * 16):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        percent = round((downloaded / total) * 100, 1)
                        if percent != last_reported:
                            last_reported = percent
                            await progress_cb(
                                stage="download", status="running",
                                message=f"Downloading {label}… {percent:.0f}%",
                                progress=percent,
                            )


def _extract(archive_path: Path, dest_dir: Path, archive_type: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_type == "tar.gz":
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dest_dir)
    else:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
    archive_path.unlink(missing_ok=True)


def _make_executable(path: Path) -> None:
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ─── Node.js ─────────────────────────────────────────────────────────────────

def _node_bin_dir(extracted_root: Path) -> Path:
    # Windows archives put node.exe/npx.cmd/npm.cmd directly at the extracted
    # root; macOS/Linux tarballs nest everything under a bin/ subdirectory.
    return extracted_root if (extracted_root / "node.exe").exists() else extracted_root / "bin"


async def _ensure_node(progress_cb: ProgressCallback) -> Path:
    """Returns the directory containing node/npx/npm, downloading+extracting if not already cached."""
    node_root = RUNTIMES_DIR / "node"
    existing = list(node_root.glob("node-v*")) if node_root.exists() else []
    for candidate in existing:
        bin_dir = _node_bin_dir(candidate)
        if (bin_dir / "node").exists() or (bin_dir / "node.exe").exists():
            await progress_cb(stage="runtime", status="running", message="Using the already-downloaded Node.js runtime.")
            return bin_dir

    await progress_cb(stage="runtime", status="running", message="Resolving the latest Node.js LTS version…")
    version = await _latest_node_lts_version()

    filename, archive_type = _node_asset_name(version)
    url = f"https://nodejs.org/dist/{version}/{filename}"
    archive_path = RUNTIMES_DIR / "_downloads" / filename

    await progress_cb(stage="download", status="running", message=f"Downloading Node.js {version}…", progress=0)
    await _download(url, archive_path, progress_cb, f"Node.js {version}")

    await progress_cb(stage="extract", status="running", message="Extracting Node.js…")
    _extract(archive_path, node_root, archive_type)

    extracted_root = node_root / filename[: -len(f".{archive_type}")]
    bin_dir = _node_bin_dir(extracted_root)
    for exe in ("node", "npx", "npm"):
        _make_executable(bin_dir / exe)

    await progress_cb(stage="runtime", status="running", message=f"Node.js {version} ready.")
    return bin_dir


# ─── uv ──────────────────────────────────────────────────────────────────────

async def _ensure_uv(progress_cb: ProgressCallback) -> Path:
    """Returns the directory containing uv/uvx, downloading+extracting if not already cached."""
    uv_root = RUNTIMES_DIR / "uv"
    existing = list(uv_root.glob("uv-*")) if uv_root.exists() else []
    for candidate in existing:
        if (candidate / "uv").exists() or (candidate / "uv.exe").exists():
            await progress_cb(stage="runtime", status="running", message="Using the already-downloaded uv runtime.")
            return candidate

    await progress_cb(stage="runtime", status="running", message="Resolving the latest uv version…")
    version = await _latest_uv_version()

    filename, archive_type = _uv_target(version)
    url = f"https://github.com/astral-sh/uv/releases/download/{version}/{filename}"
    archive_path = RUNTIMES_DIR / "_downloads" / filename

    await progress_cb(stage="download", status="running", message=f"Downloading uv {version}…", progress=0)
    await _download(url, archive_path, progress_cb, f"uv {version}")

    await progress_cb(stage="extract", status="running", message="Extracting uv…")
    extract_dir = uv_root / filename[: -len(f".{archive_type}")]
    _extract(archive_path, extract_dir, archive_type)

    # uv's archives nest one extra directory level (uv-<target>/uv-<target>/uv)
    # — physically flatten it so extract_dir itself always holds the binaries
    # directly, matching what the cache-check glob above expects to find.
    nested = list(extract_dir.glob("*/uv")) + list(extract_dir.glob("*/uv.exe"))
    if nested:
        nested_dir = nested[0].parent
        for item in nested_dir.iterdir():
            shutil.move(str(item), str(extract_dir / item.name))
        nested_dir.rmdir()

    for exe in ("uv", "uvx"):
        _make_executable(extract_dir / exe)
        _make_executable(extract_dir / f"{exe}.exe")

    await progress_cb(stage="runtime", status="running", message=f"uv {version} ready.")
    return extract_dir


# ─── Public entry point ─────────────────────────────────────────────────────

def _detect_requirement(exe: str) -> Optional[str]:
    name = Path(exe).name.lower()
    # .lower() + startswith handles 'npx.cmd'/'uv.exe' etc. showing up in a
    # pasted config that already points at a previously-resolved bundled path.
    if name in ("npx", "node", "npm") or name.startswith(("npx.", "node.", "npm.")):
        return "node"
    if name in ("uvx", "uv") or name.startswith(("uvx.", "uv.")):
        return "uv"
    return None


async def ensure_runtime(command: List[str], progress_cb: Optional[ProgressCallback] = None) -> List[str]:
    """
    Resolves `command` to something actually runnable, auto-downloading a
    portable Node.js or uv into AEGIS_DATA_DIR/runtimes when the command is
    npx/node/npm/uvx/uv and isn't already on the user's PATH. Any other
    command is checked against PATH (and, for a path-like value, checked to
    exist directly) but never auto-installed.

    Returns the resolved command list (same shape as the input, exe swapped
    for an absolute path when a runtime was bundled). Raises RuntimeError
    with a user-facing message on failure — callers surface this as the
    connect attempt's error, never a silent fallback.
    """
    progress_cb = progress_cb or _noop_progress
    if not command:
        raise RuntimeError("Empty command.")

    exe = command[0]

    # Already resolvable on the system as-is — nothing to install, don't
    # touch the network at all. Covers a user who already has Node/uv
    # installed, and non-auto-installable commands that are just... there.
    on_path = shutil.which(exe)
    if on_path or Path(exe).exists():
        await progress_cb(stage="detect", status="running", message=f"Found '{exe}' on your system.")
        return command

    requirement = _detect_requirement(exe)
    if requirement is None:
        raise RuntimeError(
            f"'{exe}' was not found on your PATH and Aegis doesn't know how to install it "
            f"automatically (only npx/node/npm and uvx/uv are auto-installed). "
            f"Install it yourself, make sure it's on PATH, and try again."
        )

    await progress_cb(stage="detect", status="running", message=f"'{exe}' isn't installed — Aegis will download a portable copy.")

    try:
        if requirement == "node":
            bin_dir = await _ensure_node(progress_cb)
        else:
            bin_dir = await _ensure_uv(progress_cb)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to download the runtime {exe} needs: {e}") from e

    exe_name = Path(exe).name
    resolved_exe = bin_dir / exe_name
    if not resolved_exe.exists():
        resolved_exe = bin_dir / f"{exe_name}.exe"
    if not resolved_exe.exists():
        raise RuntimeError(f"Downloaded the {requirement} runtime, but couldn't find '{exe_name}' inside it.")

    return [str(resolved_exe)] + command[1:]
