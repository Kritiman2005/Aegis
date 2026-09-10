"""
Aegis — Database Engine Worker Runner

Shared subprocess plumbing for every non-bundled engine (anything in
registry.py with a pip_package set — duckdb, lancedb, chromadb today).
Each call downloads/reuses a portable `uv` (via app.mcp.runtime_manager,
the same mechanism MCP server connects already use) and runs one of the
worker.py scripts under workers/ inside an isolated `uv run --with
<package>` environment, passing the operation as JSON on stdin and reading
a JSON result back from stdout — so a worker script never needs anything
importable outside its own one pip package plus the standard library.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

import anyio

from app.mcp.runtime_manager import ensure_runtime

logger = logging.getLogger(__name__)

WORKERS_DIR = Path(__file__).resolve().parent / "workers"


class WorkerError(Exception):
    """The worker subprocess ran but reported a failure, or produced no
    parseable result — as opposed to a bug in this module itself."""


def _run_sync(command: list, payload: Dict[str, Any]) -> Dict[str, Any]:
    proc = subprocess.run(
        command,
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise WorkerError(stderr[-2000:] if stderr else f"Worker exited with code {proc.returncode}.")

    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    # uv prints its own status lines to stdout on a fresh install in some
    # versions — the worker's own JSON is always the last line it prints.
    last_line = stdout.splitlines()[-1] if stdout else ""
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError:
        raise WorkerError(f"Worker produced no valid output.\n{stdout[-2000:]}")

    if not result.get("ok"):
        raise WorkerError(result.get("error") or "Worker reported failure with no message.")
    return result


async def run_worker(pip_package: str, worker_filename: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resolves/downloads uv, then runs workers/<worker_filename> with
    <pip_package> installed into its ephemeral environment, sending payload
    as JSON on stdin. Raises WorkerError on any failure — callers surface
    this the same way a direct tool-call failure is surfaced elsewhere in
    this app (per-node, never crashing the whole run)."""
    script_path = WORKERS_DIR / worker_filename
    command = await ensure_runtime(["uv", "run", "--with", pip_package, "python", str(script_path)])
    return await anyio.to_thread.run_sync(lambda: _run_sync(command, payload))
