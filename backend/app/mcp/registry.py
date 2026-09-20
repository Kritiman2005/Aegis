"""
Aegis — MCP Server Registry

Central registry managing all connected MCP servers (stdio processes).
Handles tool discovery, tool call routing, and DB sync.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.mcp.stdio_client import StdioMCPClient
from app.mcp.http_client import StreamableHTTPMCPClient

logger = logging.getLogger(__name__)

# Either client type exposes the identical start/stop/is_running/initialize/
# list_tools/call_tool/cached_tools surface — the registry never branches on
# which one it's holding.
MCPClient = "StdioMCPClient | StreamableHTTPMCPClient"


class MCPServerRegistry:
    """
    Multi-server MCP registry.
    Manages active MCP client instances (local stdio subprocesses or remote
    streamable-HTTP connections) and routes tool calls to the correct server.
    """

    def __init__(self):
        self._clients: Dict[str, object] = {}
        # tool_name -> server_name mapping
        self._tool_to_server: Dict[str, str] = {}
        # Per-server metadata (e.g. authenticated username, org, etc.)
        self._server_metadata: Dict[str, Dict[str, str]] = {}
        # Cached resources/prompts catalogs per server (the MCP list
        # entries — uri/name/description, not the content itself; that's
        # fetched on demand via read_resource/get_prompt). Most servers
        # expose neither, so these are usually empty lists, not missing
        # keys — see _finish_connect.
        self._resources: Dict[str, List[dict]] = {}
        self._prompts: Dict[str, List[dict]] = {}

    def set_server_metadata(self, server_name: str, metadata: Dict[str, str]) -> None:
        """Stores arbitrary key/value metadata for a connected server."""
        self._server_metadata[server_name] = metadata
        logger.info("Set metadata for server '%s': %s", server_name, list(metadata.keys()))

    def get_server_metadata(self, server_name: str) -> Dict[str, str]:
        """Returns stored metadata for a server, empty dict if none."""
        return self._server_metadata.get(server_name, {})

    def get_all_metadata(self) -> Dict[str, Dict[str, str]]:
        """Returns all server metadata, keyed by server name."""
        return dict(self._server_metadata)

    def _finish_connect(
        self,
        server_name: str,
        client,
        db: Optional[Session],
        server_type: str,
        config_json: Optional[dict],
    ) -> List[dict]:
        """Shared tail of connect_server/connect_remote_server: start the
        already-constructed client, run the handshake, index its tools, and
        sync to SQLite. Both callers just differ in which client class they
        hand in (StdioMCPClient vs StreamableHTTPMCPClient)."""
        if server_name in self._clients:
            self.disconnect_server(server_name, db=db)

        try:
            client.start()
            client.initialize()
            tools = client.list_tools()
        except Exception as e:
            logger.error(f"Failed to connect MCP server '{server_name}': {e}")
            client.stop()
            raise RuntimeError(f"Could not connect MCP server '{server_name}': {e}")

        self._clients[server_name] = client

        for t in tools:
            tool_name = t.get("name")
            if tool_name:
                self._tool_to_server[tool_name] = server_name

        # Best-effort — most servers declare neither capability, and a
        # server that DOES declare "resources"/"prompts" but still errors
        # on the actual list call (seen in the wild on a few early
        # implementations) shouldn't take the whole connection down over it.
        try:
            self._resources[server_name] = client.list_resources()
        except Exception as e:
            logger.warning(f"resources/list failed for '{server_name}', treating as none: {e}")
            self._resources[server_name] = []
        try:
            self._prompts[server_name] = client.list_prompts()
        except Exception as e:
            logger.warning(f"prompts/list failed for '{server_name}', treating as none: {e}")
            self._prompts[server_name] = []

        if db:
            from app.db.crud import sync_mcp_server_and_tools
            sync_mcp_server_and_tools(
                db=db,
                server_name=server_name,
                server_type=server_type,
                display_name=server_name.replace("_", " ").title(),
                tools=tools,
                config_json=config_json
            )

        return tools

    def connect_server(
        self,
        server_name: str,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        db: Optional[Session] = None,
        server_type: str = "stdio_mcp",
        config_json: Optional[dict] = None
    ) -> List[dict]:
        """
        Spawns an MCP server subprocess, performs initialization, fetches available tools,
        updates the internal routing index, and syncs tools into SQLite.
        """
        logger.info(f"Connecting MCP server '{server_name}' via command: {' '.join(command)}")
        client = StdioMCPClient(command=command, env=env)
        return self._finish_connect(server_name, client, db, server_type, config_json)

    def connect_remote_server(
        self,
        server_name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        db: Optional[Session] = None,
        config_json: Optional[dict] = None,
    ) -> List[dict]:
        """
        Connects to a hosted MCP server over the Streamable HTTP transport —
        no subprocess, no OAuth. `headers` is whatever static values the user
        pasted in themselves (an API key, a bearer token they generated on
        the server's own site); Aegis never runs an OAuth exchange.
        """
        logger.info(f"Connecting remote MCP server '{server_name}' at {url}")
        client = StreamableHTTPMCPClient(url=url, headers=headers)
        return self._finish_connect(server_name, client, db, "remote_mcp", config_json)

    def connect_google_service(self, service_name: str, credentials_json_str: str, db: Optional[Session] = None) -> List[dict]:
        """
        Convenience wrapper to launch the Google Workspace MCP Python server
        configured for a specific service (google_mail or google_drive).
        """
        if getattr(sys, 'frozen', False):
            command = [sys.executable, "mcp_google", "--service", service_name]
        else:
            main_script = Path(__file__).resolve().parent.parent.parent / "main.py"
            command = [sys.executable, str(main_script), "mcp_google", "--service", service_name]
        env = {"GOOGLE_CREDENTIALS_JSON": credentials_json_str}

        return self.connect_server(
            server_name=service_name,
            command=command,
            env=env,
            db=db,
            server_type="google_api"
        )

    def disconnect_server(self, server_name: str, db: Optional[Session] = None):
        """Stops an MCP server subprocess and updates DB status."""
        client = self._clients.pop(server_name, None)
        if client:
            client.stop()

        # Remove tools from index
        tools_to_remove = [t for t, s in self._tool_to_server.items() if s == server_name]
        for t in tools_to_remove:
            del self._tool_to_server[t]

        self._resources.pop(server_name, None)
        self._prompts.pop(server_name, None)

        if db:
            from app.db.crud import set_mcp_server_status
            set_mcp_server_status(db, server_name, status="disconnected")
            
        logger.info(f"Disconnected MCP server '{server_name}'.")

    def search_tools(self, query: str, top_k: int = 5) -> List[dict]:
        """Uses SQLite FTS5 to semantically search available tools."""
        import re
        from sqlalchemy import text
        from app.db.database import SessionLocal
        
        # Extract alphanumeric words to form a bag-of-words OR query
        words = re.findall(r'\w+', query)
        if not words:
            return self.list_all_tools()[:top_k]
            
        fts_query = " OR ".join(words)
        
        db = SessionLocal()
        try:
            sql = text("""
                SELECT name
                FROM mcp_tools_fts 
                WHERE mcp_tools_fts MATCH :match_query 
                ORDER BY rank 
                LIMIT 20
            """)
            rows = db.execute(sql, {"match_query": fts_query}).fetchall()
            
            results = []
            for row in rows:
                name = row[0]
                if name in self._tool_to_server:
                    server_name = self._tool_to_server[name]
                    client = self._clients[server_name]
                    for t in client.cached_tools:
                        if t['name'] == name:
                            results.append(dict(t))
                            break
                            
                if len(results) >= top_k:
                    break
            
            if not results:
                return self.list_all_tools()[:top_k]
                
            return results
        except Exception as e:
            logger.error(f"FTS5 tool search failed: {e}")
            return self.list_all_tools()[:top_k]
        finally:
            db.close()

    def list_all_tools(self) -> List[dict]:
        """Returns all tools from all currently connected MCP servers in standard MCP format."""
        all_tools = []
        for server_name, client in self._clients.items():
            if client.is_running:
                for tool in client.cached_tools:
                    # Make a copy so we don't mutate cache
                    t_copy = dict(tool)
                    all_tools.append(t_copy)
        return all_tools

    def get_server_for_tool(self, tool_name: str) -> Optional[str]:
        """Which connected server (== its catalog key) provides this tool, if any."""
        return self._tool_to_server.get(tool_name)

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Routes a tool call to the server providing it."""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or server_name not in self._clients:
            raise RuntimeError(f"No active MCP server registered for tool '{tool_name}'.")

        client = self._clients[server_name]
        if not client.is_running:
            raise RuntimeError(f"MCP server '{server_name}' is not running.")

        return client.call_tool(tool_name, arguments)

    def list_resources(self, server_name: str) -> List[dict]:
        """Cached resources/list catalog for one connected server (empty if
        it doesn't declare the resources capability, or isn't connected)."""
        return self._resources.get(server_name, [])

    def list_prompts(self, server_name: str) -> List[dict]:
        """Cached prompts/list catalog for one connected server (empty if
        it doesn't declare the prompts capability, or isn't connected)."""
        return self._prompts.get(server_name, [])

    def read_resource(self, server_name: str, uri: str) -> dict:
        """Fetches one resource's actual content, live — unlike tools/
        prompts, resource content isn't cached (it can be arbitrarily large
        or change on every read, e.g. a file's current contents)."""
        client = self._clients.get(server_name)
        if not client or not client.is_running:
            raise RuntimeError(f"MCP server '{server_name}' is not running.")
        return client.read_resource(uri)

    def get_prompt(self, server_name: str, name: str, arguments: Optional[Dict[str, str]] = None) -> dict:
        """Fetches a filled prompt template, live."""
        client = self._clients.get(server_name)
        if not client or not client.is_running:
            raise RuntimeError(f"MCP server '{server_name}' is not running.")
        return client.get_prompt(name, arguments)

    def get_status(self) -> Dict[str, dict]:
        """Returns health status of all registered servers."""
        status = {}
        for name, client in self._clients.items():
            status[name] = {
                "running": client.is_running,
                "server_info": client.server_info,
                "tools_count": len(client.cached_tools),
                "resources_count": len(self._resources.get(name, [])),
                "prompts_count": len(self._prompts.get(name, [])),
            }
        return status


def reconnect_from_saved_config(db: Session, server: "MCPServer") -> List[dict]:
    """
    (Re-)connects a saved MCPServer row from its stored config_json, dispatching on
    config["type"] the same way for every caller — main.py's startup auto-restore and
    the generic /api/connectors/{name}/reload endpoint both call this instead of each
    keeping their own copy of the catalog/oauth/custom dispatch logic.

    Raises on a missing/unparseable config_json or an unknown config type — callers
    decide how to surface that (log-and-skip on startup, a 4xx from the reload route).
    """
    import json as _json
    from app.mcp.catalog import resolve_connector_command

    if not server.config_json:
        raise ValueError(f"Server '{server.name}' has no saved config_json to reconnect from.")

    config = _json.loads(server.config_json)
    config_type = config.get("type")

    if config_type == "catalog":
        command = resolve_connector_command(config["server_name"], config.get("input_params") or {})
        return mcp_registry.connect_server(
            server_name=config["server_name"],
            command=command,
            env=config.get("env"),
            db=db,
            config_json=config,
        )
    elif config_type in ("oauth", "custom", "github"):
        # OAuth servers (GitHub, Slack, Notion, ...) store their resolved
        # command + access token (in env) at connect time — same shape as
        # a custom server from here on, just re-launched verbatim. A "github"
        # server (cloned+built once at install time — see github_installer.py)
        # stores its resolved local command the same way; the built files
        # are still on disk, so reconnecting just re-runs that command with
        # no re-clone/re-build needed.
        server_name = config.get("service_name") or config.get("server_name")
        return mcp_registry.connect_server(
            server_name=server_name,
            command=config["command"],
            env=config.get("env"),
            db=db,
            config_json=config,
        )
    elif config_type == "remote":
        return mcp_registry.connect_remote_server(
            server_name=config["server_name"],
            url=config["url"],
            headers=config.get("headers"),
            db=db,
            config_json=config,
        )
    else:
        raise ValueError(f"Unknown config type '{config_type}' for server '{server.name}'.")


# Global singleton registry instance
mcp_registry = MCPServerRegistry()
