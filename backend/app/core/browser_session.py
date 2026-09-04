"""
Aegis — Persistent Browser Session (Playwright, agent-driven)

Backs the browser_* local tools (navigate/click/fill/scroll/screenshot/
extract_text/tabs/close — see app/core/agents/chat.py's _browser_tool_defs)
with ONE real, long-lived headless Chromium session per conversation —
unlike web_scrape (app/core/scraper.py), which launches and tears down a
fresh browser on every single call. A persistent session is what actually
lets the agent "talk and scrape like a developer": navigate once, then
click/fill/scroll/extract against that same live page across several
separate tool calls, rather than starting from a blank page every time.

Runs through app/core/browser_driver.js, a standalone Node process that
stays alive for the session's lifetime and speaks one JSON command per
stdin line / one JSON response per stdout line. Communication is plain
blocking I/O (never asyncio) — see app/core/scraper.py's _run_driver_script
docstring for why: Playwright's own Python API (and asyncio subprocess
transport generally) was confirmed to hang unconditionally in the frozen
PyInstaller build. Callers must run BrowserSession.send() off the event
loop (anyio.to_thread.run_sync), same convention as scraper.py.

Sessions are keyed by connection_id (one browsing session per open chat
connection) and reaped after IDLE_TIMEOUT_SECONDS of inactivity by a
background thread (start_reaper, called once from main.py's startup), so
an agent that finishes browsing — or a user who just closes the tab —
doesn't leave a headless Chromium process running forever.
"""

import collections
import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

IDLE_TIMEOUT_SECONDS = 600  # 10 minutes since the session's last command


class BrowserSession:
    def __init__(self, connection_id: str):
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        from app.core.scraper import BROWSERS_DIR

        self.connection_id = connection_id
        self.last_used = time.time()
        self._lock = threading.Lock()
        self._next_id = 0
        # Bounded ring buffer of the driver's stderr — never blocks on a
        # full pipe (which would otherwise risk the Node process stalling
        # mid-write and this session hanging forever on its next read),
        # and gives something real to show if the process dies unexpectedly.
        self._stderr_tail: collections.deque = collections.deque(maxlen=200)

        node_path, cli_path = compute_driver_executable()
        playwright_core_index = str(Path(cli_path).parent / "index.js")
        driver_script = str(Path(__file__).parent / "browser_driver.js")

        self._proc = subprocess.Popen(
            [node_path, driver_script, playwright_core_index],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered, so a flush() on our side is promptly readable on theirs
            env={**get_driver_env(), "PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS_DIR)},
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        try:
            for line in iter(self._proc.stderr.readline, ""):
                self._stderr_tail.append(line.rstrip())
        except Exception:
            pass

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def send(self, action: str, params: Optional[Dict] = None) -> Dict:
        """
        Blocking — writes one command line, blocks for the matching
        response line. Call only from a worker thread
        (anyio.to_thread.run_sync), never directly from an async def.

        Only one command is ever in flight per session: this lock is what
        makes that guarantee true, which is what lets browser_driver.js
        assume the next stdout line is always the current command's
        response, with no request/response id matching needed on its side.
        """
        with self._lock:
            self.last_used = time.time()
            if not self.is_alive():
                tail = "\n".join(self._stderr_tail)[-2000:]
                raise RuntimeError(f"Browser session process has exited.{(' ' + tail) if tail else ''}")

            self._next_id += 1
            payload = json.dumps({"id": self._next_id, "action": action, "params": params or {}})
            try:
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"Browser session process is not accepting input: {e}")

            line = self._proc.stdout.readline()
            if not line:
                tail = "\n".join(self._stderr_tail)[-2000:]
                raise RuntimeError(f"Browser session process closed unexpectedly.{(' ' + tail) if tail else ''}")
            try:
                resp = json.loads(line.strip())
            except json.JSONDecodeError:
                raise RuntimeError(f"Malformed response from browser session: {line[:500]}")
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error") or "Unknown browser session error")
            return resp

    def close(self):
        """Blocking — best-effort graceful close, force-killed if unresponsive."""
        with self._lock:
            if self.is_alive():
                try:
                    self._proc.stdin.write(json.dumps({"id": 0, "action": "close", "params": {}}) + "\n")
                    self._proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()


_sessions: Dict[str, BrowserSession] = {}
_sessions_lock = threading.Lock()


def get_session(connection_id: str) -> BrowserSession:
    """Returns the connection's existing session, or launches a new one if
    there isn't one yet (or the previous one's process has died)."""
    with _sessions_lock:
        session = _sessions.get(connection_id)
        if session is None or not session.is_alive():
            session = BrowserSession(connection_id)
            _sessions[connection_id] = session
        return session


def close_session(connection_id: str) -> None:
    session = None
    with _sessions_lock:
        session = _sessions.pop(connection_id, None)
    if session:
        session.close()


def close_all_sessions() -> None:
    """Called from main.py's shutdown event so no headless Chromium process
    outlives the backend itself."""
    with _sessions_lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for s in sessions:
        s.close()


def _reap_idle_sessions() -> None:
    while True:
        time.sleep(60)
        now = time.time()
        to_close = []
        with _sessions_lock:
            stale_ids = [cid for cid, s in _sessions.items() if now - s.last_used > IDLE_TIMEOUT_SECONDS]
            for cid in stale_ids:
                to_close.append((cid, _sessions.pop(cid)))
        for cid, session in to_close:
            logger.info(f"[browser_session] Closing idle browser session for connection {cid}")
            session.close()


def start_reaper() -> None:
    """Call once at app startup (main.py's on_startup)."""
    threading.Thread(target=_reap_idle_sessions, daemon=True).start()
