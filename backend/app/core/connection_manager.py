"""
Aegis — WebSocket Connection Manager

Manages active WebSocket connections. Supports:
  - Multiple concurrent connections (multi-tab or multi-window)
  - Per-connection messaging
  - Broadcast messaging
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
    or auto-generated). This allows Aegis to route messages to specific connections
    when multiple windows or tabs are open.
    """

    def __init__(self) -> None:
        # Maps connection_id → WebSocket instance
        self._connections: Dict[str, WebSocket] = {}

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, connection_id: str) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections[connection_id] = websocket
        logger.info(f"[WS] Connected: {connection_id} (total: {self.count})")

    def disconnect(self, connection_id: str, websocket: WebSocket = None) -> None:
        """Remove a connection from the registry."""
        if connection_id in self._connections:
            if websocket and self._connections[connection_id] != websocket:
                # A new connection has already taken over this ID
                return
            del self._connections[connection_id]
            logger.info(f"[WS] Disconnected: {connection_id} (total: {self.count})")

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def send(self, connection_id: str, message: str) -> bool:
        """
        Send a text message to a specific connection.
        Returns False if the connection no longer exists.
        """
        ws = self._connections.get(connection_id)
        if ws is None:
            return False
        try:
            await ws.send_text(message)
            return True
        except Exception as exc:
            logger.warning(f"[WS] Send failed for {connection_id}: {exc}")
            self.disconnect(connection_id, ws)
            return False

    async def send_json(self, connection_id: str, data: dict) -> bool:
        """Send a JSON-serializable dict to a specific connection."""
        ws = self._connections.get(connection_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except RuntimeError as exc:
            if "Unexpected ASGI message" in str(exc):
                # Starlette/FastAPI raises this if the endpoint has already returned
                # (meaning the client disconnected). It's a normal lifecycle event.
                self.disconnect(connection_id, ws)
                return False
            logger.warning(f"[WS] JSON send failed for {connection_id}: {exc}")
            self.disconnect(connection_id, ws)
            return False
        except Exception as exc:
            import traceback
            tb = "".join(traceback.format_stack())
            logger.warning(f"[WS] JSON send failed for {connection_id}: {exc}\n{tb}")
            self.disconnect(connection_id, ws)
            return False

    async def broadcast(self, message: str) -> None:
        """Send a text message to ALL active connections."""
        disconnected: Set[str] = set()

        for connection_id, ws in list(self._connections.items()):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(connection_id)

        for cid in disconnected:
            self.disconnect(cid)

    async def broadcast_json(self, data: dict) -> None:
        """Broadcast a JSON payload to ALL active connections."""
        disconnected: Set[str] = set()

        for connection_id, ws in list(self._connections.items()):
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.add(connection_id)

        for cid in disconnected:
            self.disconnect(cid)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Number of active connections."""
        return len(self._connections)

    @property
    def connection_ids(self) -> list[str]:
        """List of all active connection IDs."""
        return list(self._connections.keys())


# ── Singleton Instance ────────────────────────────────────────────────────────
# Shared across all WebSocket route handlers in the same process.

manager = ConnectionManager()
