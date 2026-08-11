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

logger = logging.getLogger(__name__)


class MCPServerRegistry:
    """
    Multi-server MCP registry.
    Manages active StdioMCPClient instances and routes tool calls to the correct server.
    """

    def __init__(self):
        self._clients: Dict[str, StdioMCPClient] = {}
        # tool_name -> server_name mapping
        self._tool_to_server: Dict[str, str] = {}
        # Per-server metadata (e.g. authenticated username, org, etc.)
        self._server_metadata: Dict[str, Dict[str, str]] = {}

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
        # If server is already running, stop it first to refresh
        if server_name in self._clients:
            self.disconnect_server(server_name, db=db)

        logger.info(f"Connecting MCP server '{server_name}' via command: {' '.join(command)}")
        client = StdioMCPClient(command=command, env=env)
        
        try:
            client.start()
            client.initialize()
            tools = client.list_tools()
        except Exception as e:
            logger.error(f"Failed to connect MCP server '{server_name}': {e}")
            client.stop()
            raise RuntimeError(f"Could not connect MCP server '{server_name}': {e}")

        self._clients[server_name] = client

        # Update index
        for t in tools:
            tool_name = t.get("name")
            if tool_name:
                self._tool_to_server[tool_name] = server_name

        # Sync to DB if session provided
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

    def connect_google_workspace(self, credentials_json_str: str, db: Optional[Session] = None) -> List[dict]:
        """
        Convenience wrapper to launch the Google Workspace MCP Python server.
        """
        google_script = Path(__file__).resolve().parent / "servers" / "google_mcp_server.py"
        command = [sys.executable, str(google_script)]
        env = {"GOOGLE_CREDENTIALS_JSON": credentials_json_str}

        return self.connect_server(
            server_name="google_workspace",
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

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Routes a tool call to the server providing it."""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name or server_name not in self._clients:
            raise RuntimeError(f"No active MCP server registered for tool '{tool_name}'.")

        client = self._clients[server_name]
        if not client.is_running:
            raise RuntimeError(f"MCP server '{server_name}' is not running.")

        return client.call_tool(tool_name, arguments)

    def get_status(self) -> Dict[str, dict]:
        """Returns health status of all registered servers."""
        status = {}
        for name, client in self._clients.items():
            status[name] = {
                "running": client.is_running,
                "server_info": client.server_info,
                "tools_count": len(client.cached_tools)
            }
        return status


# Global singleton registry instance
mcp_registry = MCPServerRegistry()
