"""
Aegis — MCP GitHub Repo Installer

Lets a user install an MCP server straight from a GitHub repo URL instead of
needing it already published to the npm/pypi registries the catalog and
registry search cover (FLUJO's GitHubTab does the same thing). Clones the
repo, looks for a package.json or a Python project file, and works out a
best-guess run command + any build/install steps it needs first.

Deliberately conservative, matching runtime_manager's "honest about what it
can't do" pattern: only the two common, unambiguous shapes are auto-detected
(a Node package with a `bin` entry or `main`, a Python project with a
`[project.scripts]` entry or an obvious top-level server script). Anything
else comes back with `command: None` and a `reason` explaining why, so the
caller can ask the user to type the run command in themselves rather than
silently guessing wrong and connecting to the wrong process.

No OAuth, no GitHub API token required for a public repo — this shells out
to the user's own `git`, the same as cloning by hand.
"""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]

_data_dir = os.environ.get("AEGIS_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPOS_DIR = Path(_data_dir) / "connector_repos" if _data_dir else BASE_DIR / "connector_repos"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


async def _noop_progress(**kwargs) -> None:
    pass


def _repo_slug(repo_url: str) -> str:
    """owner/repo (or the last two path segments) -> a filesystem-safe dir name."""
    cleaned = repo_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    parts = [p for p in cleaned.split("/") if p][-2:]
    slug = "-".join(parts) if parts else "repo"
    return _SAFE_NAME_RE.sub("-", slug).strip("-") or "repo"


def repo_dir_for(repo_url: str) -> Path:
    return REPOS_DIR / _repo_slug(repo_url)


async def _run(cmd: List[str], cwd: Path, progress_cb: ProgressCallback, label: str) -> None:
    await progress_cb(stage="build", status="running", message=label)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    if proc.returncode != 0:
        tail = output.decode(errors="replace")[-1500:] if output else ""
        raise RuntimeError(f"'{' '.join(cmd)}' failed (exit {proc.returncode}): {tail}")


async def clone_or_update(repo_url: str, progress_cb: Optional[ProgressCallback] = None) -> Path:
    """Clones `repo_url` into REPOS_DIR (shallow), or pulls latest if already
    cloned here before. Raises RuntimeError with git's own output on failure."""
    progress_cb = progress_cb or _noop_progress
    if not shutil.which("git"):
        raise RuntimeError("git isn't installed — it's required to install a server from GitHub.")

    dest = repo_dir_for(repo_url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if (dest / ".git").exists():
        await progress_cb(stage="clone", status="running", message=f"Updating existing clone of {repo_url}…")
        await _run(["git", "pull", "--ff-only"], dest, progress_cb, f"Pulling latest from {repo_url}…")
    else:
        if dest.exists():
            shutil.rmtree(dest)  # a stale, non-git leftover from an earlier failed attempt
        await progress_cb(stage="clone", status="running", message=f"Cloning {repo_url}…")
        await _run(["git", "clone", "--depth", "1", repo_url, str(dest)], dest.parent, progress_cb, f"Cloning {repo_url}…")

    return dest


def _detect_node(repo_dir: Path) -> Optional[Dict[str, Any]]:
    pkg_path = repo_dir / "package.json"
    if not pkg_path.exists():
        return None

    try:
        pkg = json.loads(pkg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    entry: Optional[str] = None
    bin_field = pkg.get("bin")
    if isinstance(bin_field, str):
        entry = bin_field
    elif isinstance(bin_field, dict) and bin_field:
        entry = next(iter(bin_field.values()))
    elif pkg.get("main"):
        entry = pkg["main"]

    if not entry:
        return None

    needs_build = bool((pkg.get("scripts") or {}).get("build"))
    # A build script typically emits to dist/ — if the declared entry point
    # doesn't exist yet (only true pre-build), assume it lands under dist/
    # with the same basename, the overwhelmingly common convention.
    entry_path = repo_dir / entry
    if needs_build and not entry_path.exists() and not entry.startswith("dist/"):
        entry = f"dist/{Path(entry).name}"

    build_steps = [["npm", "install"]]
    if needs_build:
        build_steps.append(["npm", "run", "build"])

    return {
        "runtime": "node",
        # Absolute path — StdioMCPClient spawns the process with the
        # backend's own cwd, not repo_dir, so a relative entry wouldn't resolve.
        "command": ["node", str(repo_dir / entry)],
        "build_steps": build_steps,
        "needs_build": needs_build,
    }


def _detect_python(repo_dir: Path) -> Optional[Dict[str, Any]]:
    pyproject = repo_dir / "pyproject.toml"
    script_entry: Optional[str] = None

    if pyproject.exists():
        try:
            text = pyproject.read_text()
        except OSError:
            text = ""
        # Minimal [project.scripts] scan — good enough to pick the (usual)
        # single declared console script without pulling in a TOML dependency.
        m = re.search(r"\[project\.scripts\]\s*\n\s*([A-Za-z0-9_-]+)\s*=", text)
        if m:
            script_entry = m.group(1)

    if script_entry:
        return {
            "runtime": "python",
            "command": ["uv", "run", "--project", str(repo_dir), script_entry],
            "build_steps": [["uv", "sync"]] if pyproject.exists() else [],
            "needs_build": pyproject.exists(),
        }

    # No declared console script — fall back to an obvious top-level entry
    # script, the other common shape for a small single-file MCP server.
    for candidate in ("server.py", "main.py", "__main__.py"):
        if (repo_dir / candidate).exists():
            build_steps = [["pip", "install", "-r", "requirements.txt"]] if (repo_dir / "requirements.txt").exists() else []
            return {
                "runtime": "python",
                "command": ["python", str(repo_dir / candidate)],
                "build_steps": build_steps,
                "needs_build": bool(build_steps),
            }

    return None


def detect_run_config(repo_dir: Path) -> Dict[str, Any]:
    """
    Best-guess run configuration for a cloned repo. Returns
    {"command": [...], "build_steps": [[...], ...], "runtime": "node"|"python", "detected": True}
    on success, or {"command": None, "detected": False, "reason": "..."} when
    nothing recognizable was found — the caller should let the user type the
    command in by hand rather than connecting to a guess.
    """
    node = _detect_node(repo_dir)
    if node:
        return {**node, "detected": True}

    python = _detect_python(repo_dir)
    if python:
        return {**python, "detected": True}

    return {
        "command": None,
        "build_steps": [],
        "runtime": None,
        "detected": False,
        "reason": (
            "Couldn't find a package.json with a bin/main entry or a Python "
            "project script — enter the run command yourself (relative to the "
            "cloned repo) and Aegis will still start and connect it."
        ),
    }


async def build(repo_dir: Path, build_steps: List[List[str]], progress_cb: Optional[ProgressCallback] = None) -> None:
    """Runs each build/install step in order inside repo_dir. Raises RuntimeError
    with the failing command's tail output on the first failure."""
    progress_cb = progress_cb or _noop_progress
    for step in build_steps:
        exe = step[0]
        if not shutil.which(exe):
            raise RuntimeError(
                f"'{exe}' isn't installed or on your PATH — it's required to build this server "
                f"(needed for: {' '.join(step)})."
            )
        await _run(step, repo_dir, progress_cb, f"Running '{' '.join(step)}'…")
