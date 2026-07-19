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
from pathlib import Path
from typing import Any, Dict, List, Optional

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

        # Populated after initialize()
        self.server_info: Dict = {}
        self.server_capabilities: Dict = {}

    # ── Process lifecycle ────────────────────────────────────────────────────

    def start(self):
        """Spawn the MCP server subprocess."""
        merged_env = {**os.environ, **self.env}

        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # stderr captured separately — never mixed into stdout
            env=merged_env,
            text=True,
            bufsize=1,                # Line-buffered
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
        Send a JSON-RPC request over stdin and read one JSON-RPC response from stdout.
        Raises RuntimeError on timeout or closed pipe.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP server is not running.")

        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

        # Read one response line (blocking)
        response_line = self._process.stdout.readline()
        if not response_line:
            stderr_output = self._process.stderr.read() if self._process.stderr else ""
            raise RuntimeError(
                f"MCP server stdout closed unexpectedly. stderr: {stderr_output[:500]}"
            )

        return json.loads(response_line.strip())

    def _send_notification(self, method: str, params: Optional[dict] = None):
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        line = json.dumps(msg, ensure_ascii=False) + "\n"
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

        # Flatten MCP content array → plain text
        parts = []
        for item in content:
            item_type = item.get("type", "")
            if item_type == "text":
                parts.append(item.get("text", ""))
            elif item_type == "resource":
                parts.append(json.dumps(item.get("resource", {})))
            elif item_type == "image":
                parts.append(f"[image: {item.get('url', 'embedded')}]")
        
        return "\n".join(parts) if parts else str(result)

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

    @property
    def cached_tools(self) -> List[dict]:
        """Return the tool list cached after the last list_tools() call."""
        return self._cached_tools
