"""
Aegis — Streamable HTTP MCP Client

Connects to a remote MCP server over the "Streamable HTTP" transport (the
single-endpoint POST/SSE transport the official MCP spec, Claude Desktop,
and FLUJO's RemoteTab all use for hosted servers) instead of spawning a
local subprocess. Duck-types StdioMCPClient's public surface (start/stop/
is_running/initialize/list_tools/call_tool/cached_tools/server_info/
server_capabilities) so MCPServerRegistry can hold either kind of client
interchangeably without branching on type at the call sites.

Deliberately excludes any OAuth flow — Aegis doesn't run a hosted redirect
broker (see app.core.feature_flags.CONNECTORS_ENABLED's docstring for why).
What IS supported is a static header the user pastes in themselves (e.g.
an "Authorization: Bearer <token>" they generated on the server's own
site, or the API-key header a registry listing says it needs) — that's
not an OAuth-acting behavior since Aegis never talks to a token endpoint
or handles a redirect, it just forwards a header value the user already
has.
"""

import json
import logging
import threading
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, read=60.0)


class StreamableHTTPMCPClient:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout

        self._client: Optional[httpx.Client] = None
        self._session_id: Optional[str] = None
        self._request_id = 0
        self._cached_tools: List[Dict] = []
        # Same rationale as StdioMCPClient's lock: one shared client per
        # server, serialize the full send+recv round trip.
        self._io_lock = threading.Lock()

        self.server_info: Dict = {}
        self.server_capabilities: Dict = {}

    # ── Process lifecycle (duck-typed to match StdioMCPClient) ────────────────

    def start(self):
        self._client = httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        logger.info(f"[MCP] Connecting to remote server at {self.url}")

    def stop(self):
        if self._client:
            self._client.close()
        self._client = None

    @property
    def is_running(self) -> bool:
        return self._client is not None

    # ── Transport ────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, message: dict) -> dict:
        if not self._client:
            raise RuntimeError("Remote MCP server is not connected.")

        request_id = message.get("id")
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        with self._io_lock:
            try:
                resp = self._client.post(self.url, json=message, headers=headers)
            except httpx.HTTPError as e:
                raise RuntimeError(f"Could not reach remote MCP server: {e}") from e

            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Remote MCP server returned HTTP {resp.status_code}: {resp.text[:300]}"
                )

            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id

            if request_id is None:
                return {}  # notification — no response body expected

            if resp.status_code == 202 or not resp.content:
                return {}

            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return self._parse_sse(resp.text, request_id)
            return resp.json()

    def _parse_sse(self, body: str, request_id) -> dict:
        """Extracts the JSON-RPC response matching request_id out of an SSE
        body — the server may emit several `data:` events before the one
        that actually answers this request."""
        last: Optional[dict] = None
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            last = parsed
            if parsed.get("id") == request_id:
                return parsed
        if last is not None:
            return last
        raise RuntimeError("Remote MCP server sent an empty or unparseable event stream.")

    def _send_notification(self, method: str, params: Optional[dict] = None):
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._post(msg)

    # ── MCP Protocol (identical shape to StdioMCPClient) ───────────────────────

    def initialize(self) -> dict:
        response = self._post({
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
                f"[MCP] Initialized remote server: {self.server_info.get('name', '?')} "
                f"v{self.server_info.get('version', '?')} | "
                f"capabilities: {list(self.server_capabilities.keys())}"
            )
        elif "error" in response:
            raise RuntimeError(f"Remote MCP initialize failed: {response['error']}")

        self._send_notification("notifications/initialized")
        return response

    def list_tools(self) -> List[dict]:
        response = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })

        if "error" in response:
            raise RuntimeError(f"tools/list failed: {response['error']}")

        tools = response.get("result", {}).get("tools", [])
        self._cached_tools = tools
        logger.info(f"[MCP] Discovered {len(tools)} tools on remote server: {[t['name'] for t in tools]}")
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        response = self._post({
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
        if "resources" not in self.server_capabilities:
            return []
        response = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "resources/list",
            "params": {},
        })
        return response.get("result", {}).get("resources", [])

    def list_prompts(self) -> List[dict]:
        if "prompts" not in self.server_capabilities:
            return []
        response = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "prompts/list",
            "params": {},
        })
        return response.get("result", {}).get("prompts", [])

    @property
    def cached_tools(self) -> List[dict]:
        return self._cached_tools
