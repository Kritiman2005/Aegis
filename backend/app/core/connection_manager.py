"""
Aegis — WebSocket Connection Manager

Manages active WebSocket connections. Supports:
  - Multiple concurrent connections (multi-tab or multi-window) sharing the same session ID
  - Per-connection (session) messaging broadcasting to all tabs
  - Broadcast messaging to all users
  - Clean disconnect handling
"""

import asyncio
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Thread-safe WebSocket connection registry.

    Each connection is identified by a unique string ID (passed from the client
    or auto-generated). This allows Aegis to route messages to specific connections.
    Because multiple windows or tabs might share the same session ID, it supports
    multiple WebSockets per connection ID.
    """

    def __init__(self) -> None:
        # Maps connection_id → Set of WebSocket instances
        self._connections: Dict[str, Set[WebSocket]] = {}

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, connection_id: str) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        if connection_id not in self._connections:
            self._connections[connection_id] = set()
        self._connections[connection_id].add(websocket)
        logger.info(f"[WS] Connected: {connection_id} (total sessions: {self.count})")

    def disconnect(self, connection_id: str, websocket: WebSocket = None) -> None:
        """Remove a connection from the registry."""
        if connection_id in self._connections:
            if websocket:
                if websocket in self._connections[connection_id]:
                    self._connections[connection_id].remove(websocket)
                if not self._connections[connection_id]:
                    del self._connections[connection_id]
                    logger.info(f"[WS] Disconnected session: {connection_id} (total sessions: {self.count})")
            else:
                # Disconnect all websockets for this ID if none specified
                del self._connections[connection_id]
                logger.info(f"[WS] Disconnected session completely: {connection_id} (total sessions: {self.count})")

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def send(self, connection_id: str, message: str) -> bool:
        """
        Send a text message to all websockets for a specific connection ID.
        Returns False if the connection no longer exists.
        """
        websockets = self._connections.get(connection_id)
        if not websockets:
            return False
            
        success = False
        # Create list copy to avoid size changed during iteration errors
        for ws in list(websockets):
            try:
                await ws.send_text(message)
                success = True
            except Exception as exc:
                logger.warning(f"[WS] Send failed for {connection_id}: {exc}")
                self.disconnect(connection_id, ws)
        return success

    async def send_json(self, connection_id: str, data: dict) -> bool:
        """Send a JSON-serializable dict to all websockets for a specific connection ID."""
        websockets = self._connections.get(connection_id)
        if not websockets:
            return False
            
        success = False
        for ws in list(websockets):
            try:
                await ws.send_json(data)
                success = True
            except RuntimeError as exc:
                if "Unexpected ASGI message" in str(exc):
                    # Starlette/FastAPI raises this if the endpoint has already returned
                    # (meaning the client disconnected). It's a normal lifecycle event.
                    self.disconnect(connection_id, ws)
                else:
                    logger.warning(f"[WS] JSON send failed for {connection_id}: {exc}")
                    self.disconnect(connection_id, ws)
            except Exception as exc:
                import traceback
                tb = "".join(traceback.format_stack())
                logger.warning(f"[WS] JSON send failed for {connection_id}: {exc}\n{tb}")
                self.disconnect(connection_id, ws)
        return success

    async def broadcast(self, message: str) -> None:
        """Send a text message to ALL active connections."""
        for connection_id, websockets in list(self._connections.items()):
            for ws in list(websockets):
                try:
                    await ws.send_text(message)
                except Exception:
                    self.disconnect(connection_id, ws)

    async def broadcast_json(self, data: dict) -> None:
        """Broadcast a JSON payload to ALL active connections."""
        for connection_id, websockets in list(self._connections.items()):
            for ws in list(websockets):
                try:
                    await ws.send_json(data)
                except Exception:
                    self.disconnect(connection_id, ws)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Number of active sessions."""
        return len(self._connections)

    @property
    def connection_ids(self) -> list[str]:
        """List of all active connection IDs."""
        return list(self._connections.keys())


# ── Singleton Instance ────────────────────────────────────────────────────────
# Shared across all WebSocket route handlers in the same process.

manager = ConnectionManager()
