"""
Aegis — stdio MCP Client

Connects to any MCP server that speaks JSON-RPC 2.0 over stdin/stdout.
Spawns the server as a subprocess and manages the full protocol lifecycle:
  initialize → notifications/initialized → tools/list → tools/call → shutdown
"""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.mcp.content import flatten_content_item

logger = logging.getLogger(__name__)


class StdioMCPClient:
    """
    MCP client that communicates with an MCP server via stdio transport.

    Args:
        command:  The command to spawn (e.g. ["npx", "-y", "@modelcontextprotocol/server-github"]
                  or ["python", "/path/to/google_mcp_server.py"])
        env:      Extra environment variables to pass to the subprocess.
                  Sensitive values like tokens live here (e.g. {"GITHUB_TOKEN": "ghp_xxx"}).
        timeout:  Seconds to wait for a single response before giving up.
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ):
        self.command = command
        self.env = env or {}
        self.timeout = timeout

        self._process: Optional[subprocess.Popen] = None
        self._request_id: int = 0
        self._cached_tools: List[Dict] = []
        # Serializes send+recv over this server's single stdin/stdout pipe pair.
        # mcp_registry keeps one StdioMCPClient per server as a process-wide
        # singleton shared by every chat session; without this lock, two sessions
        # calling tools on the same server concurrently could interleave their
        # writes/reads and each end up receiving the other's response.
        self._io_lock = threading.Lock()

        # Populated after initialize()
        self.server_info: Dict = {}
        self.server_capabilities: Dict = {}

    # ── Process lifecycle ────────────────────────────────────────────────────

    def start(self):
        """Spawn the MCP server subprocess."""
        merged_env = {**os.environ, **self.env}

        # npx/npm — whether resolved from PATH or auto-downloaded by
        # runtime_manager.ensure_runtime — are .cmd wrapper scripts on
        # Windows, not real PE executables. CreateProcess can't launch one
        # directly (fails with WinError 193, "%1 is not a valid Win32
        # application"); it needs to go through cmd.exe. shell=True with a
        # list of args is the documented way to do that on Windows —
        # Python still converts the list into a properly quoted command
        # line (subprocess.list2cmdline) rather than handing raw text to
        # the shell, same as the shell=False path below.
        use_shell = sys.platform == "win32" and self.command and self.command[0].lower().endswith((".cmd", ".bat"))

        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # stderr captured separately — never mixed into stdout
            env=merged_env,
            text=True,
            bufsize=1,                # Line-buffered
            shell=use_shell,
        )
        logger.info(f"[MCP] Spawned: {' '.join(self.command)} (PID {self._process.pid})")

    def stop(self):
        """Gracefully terminate the MCP server subprocess."""
        if not self._process:
            return
        try:
            if self._process.stdin:
                self._process.stdin.close()
            self._process.terminate()
            self._process.wait(timeout=5)
            logger.info(f"[MCP] Server stopped (PID {self._process.pid})")
        except Exception as e:
            logger.warning(f"[MCP] Error stopping server: {e} — sending SIGKILL")
            self._process.kill()
        finally:
            self._process = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ── Transport ────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_recv(self, message: dict) -> dict:
        """
        Send a JSON-RPC request over stdin and read the matching JSON-RPC response
        from stdout. Raises RuntimeError on timeout or closed pipe.

        Holds _io_lock for the full write+read round trip so concurrent callers
        (e.g. two chat sessions calling tools on the same MCP server) can't
        interleave their writes or steal each other's response line.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP server is not running.")

        request_id = message.get("id")
        line = json.dumps(message, ensure_ascii=False) + "\n"

        with self._io_lock:
            self._process.stdin.write(line)
            self._process.stdin.flush()

            while True:
                response_line = self._process.stdout.readline()
                if not response_line:
                    stderr_output = self._process.stderr.read() if self._process.stderr else ""
                    raise RuntimeError(
                        f"MCP server stdout closed unexpectedly. stderr: {stderr_output[:500]}"
                    )

                response = json.loads(response_line.strip())
                # Defensive: with the lock held for the whole round trip this should
                # always match on the first line, but skip anything that doesn't
                # (e.g. a stray server-initiated notification) rather than handing
                # a mismatched response back to the caller.
                if request_id is None or response.get("id") == request_id:
                    return response
                logger.warning(
                    f"[MCP] Discarding response id={response.get('id')!r}, expected {request_id!r}"
                )

    def _send_notification(self, method: str, params: Optional[dict] = None):
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._io_lock:
            self._process.stdin.write(line)
            self._process.stdin.flush()

    # ── MCP Protocol ────────────────────────────────────────────────────────

    def initialize(self) -> dict:
        """
        Perform the MCP initialization handshake.
        Must be called after start() and before any tool calls.
        """
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Aegis", "version": "1.0.0"},
            },
        })

        if "result" in response:
            self.server_info = response["result"].get("serverInfo", {})
            self.server_capabilities = response["result"].get("capabilities", {})
            logger.info(
                f"[MCP] Initialized server: {self.server_info.get('name', '?')} "
                f"v{self.server_info.get('version', '?')} | "
                f"capabilities: {list(self.server_capabilities.keys())}"
            )
        elif "error" in response:
            raise RuntimeError(f"MCP initialize failed: {response['error']}")

        # Required: send initialized notification so server can start accepting requests
        self._send_notification("notifications/initialized")
        return response

    def list_tools(self) -> List[dict]:
        """
        Fetch the list of tools from the server (tools/list).
        Results are cached for use in registry routing.
        """
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })

        if "error" in response:
            raise RuntimeError(f"tools/list failed: {response['error']}")

        tools = response.get("result", {}).get("tools", [])
        self._cached_tools = tools
        logger.info(f"[MCP] Discovered {len(tools)} tools: {[t['name'] for t in tools]}")
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """
        Call a tool on the server (tools/call).
        Returns the flattened text content of the result.
        Raises RuntimeError on protocol or tool error.
        """
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

        if "error" in response:
            raise RuntimeError(
                f"Tool '{name}' error: {response['error'].get('message', str(response['error']))}"
            )

        result = response.get("result", {})
        content = result.get("content", [])

        parts = [flatten_content_item(item) for item in content]
        return "\n".join(p for p in parts if p) if parts else str(result)

    def list_resources(self) -> List[dict]:
        """Fetch resources exposed by this server (if supported)."""
        if "resources" not in self.server_capabilities:
            return []
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/list",
            "params": {},
        })
        return response.get("result", {}).get("resources", [])

    def list_prompts(self) -> List[dict]:
        """Fetch prompt templates exposed by this server (if supported)."""
        if "prompts" not in self.server_capabilities:
            return []
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "prompts/list",
            "params": {},
        })
        return response.get("result", {}).get("prompts", [])

    def read_resource(self, uri: str) -> dict:
        """
        Fetch one resource's actual content (resources/read) — list_resources
        only returns the catalog entry (uri/name/description/mimeType), not
        the content itself. Returns the raw MCP result: {"contents": [...]},
        each entry having `text` (for text resources) or `blob` (base64, for
        binary ones) alongside its own `uri`/`mimeType`.
        """
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/read",
            "params": {"uri": uri},
        })
        if "error" in response:
            raise RuntimeError(f"resources/read failed for '{uri}': {response['error']}")
        return response.get("result", {})

    def get_prompt(self, name: str, arguments: Optional[dict] = None) -> dict:
        """
        Fetch a filled prompt template (prompts/get) — list_prompts only
        returns the catalog entry (name/description/arguments schema), not
        the actual rendered messages. Returns the raw MCP result:
        {"description": ..., "messages": [{"role": ..., "content": {...}}]}.
        """
        response = self._send_recv({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "prompts/get",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if "error" in response:
            raise RuntimeError(f"prompts/get failed for '{name}': {response['error']}")
        return response.get("result", {})

    @property
    def cached_tools(self) -> List[dict]:
        """Return the tool list cached after the last list_tools() call."""
        return self._cached_tools
