"""
Aegis — WebSocket Endpoint (/ws)

Handles the persistent WebSocket connection from the Next.js frontend.

Protocol:
  Client → Server (JSON):
    { "type": "message", "content": "user text", "connection_id": "uuid" }
    { "type": "ping" }

  Server → Client (JSON):
    { "type": "token",  "content": "partial text" }   ← streaming tokens
    { "type": "done",   "content": "" }                ← stream complete
    { "type": "error",  "content": "error message" }   ← error occurred
    { "type": "pong" }                                  ← ping response

Replace the `generate_response()` stub with your LLM integration (Ollama, llama.cpp, etc.)
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.connection_manager import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])


# ─── LLM Stub ────────────────────────────────────────────────────────────────

async def generate_response(user_message: str) -> AsyncGenerator[str, None]:
    """
    ⚡ STUB: Replace this function with your actual LLM integration.

    Examples:
      - Ollama:    POST http://localhost:11434/api/generate (stream=True)
      - llama.cpp: POST http://localhost:8080/completion    (stream=True)
      - OpenAI:    client.chat.completions.create(stream=True)

    Yields individual tokens (strings) for real-time streaming to the client.
    """
    # Simulate a streaming response, token by token
    demo_response = (
        f"[Aegis stub response] You said: '{user_message}'. "
        "Replace `generate_response()` in backend/app/api/websocket.py "
        "with your local LLM integration to enable real AI responses."
    )

    for token in demo_response.split():
        yield token + " "
        await asyncio.sleep(0.04)  # Simulates ~25 tokens/sec streaming rate


# ─── WebSocket Endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str = Query(default_factory=lambda: str(uuid.uuid4())),
) -> None:
    """
    Main WebSocket endpoint.

    Clients may supply a `client_id` query param to maintain session identity
    across reconnects:  ws://localhost:8000/ws?client_id=<uuid>
    """
    connection_id = client_id
    await manager.connect(websocket, connection_id)

    # Notify client of their assigned connection ID
    await manager.send_json(connection_id, {
        "type": "connected",
        "connection_id": connection_id,
    })

    try:
        while True:
            raw = await websocket.receive_text()

            # ── Parse Incoming Message ────────────────────────────────────────
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_json(connection_id, {
                    "type": "error",
                    "content": "Invalid JSON payload.",
                })
                continue

            msg_type = payload.get("type", "message")
            content = payload.get("content", "")

            # ── Handle Ping ───────────────────────────────────────────────────
            if msg_type == "ping":
                await manager.send_json(connection_id, {"type": "pong"})
                continue

            # ── Handle User Message → Stream Response ─────────────────────────
            if msg_type == "message" and content.strip():
                logger.info(f"[WS:{connection_id[:8]}] User: {content[:80]!r}")

                try:
                    # Stream tokens back to the client
                    async for token in generate_response(content):
                        sent = await manager.send_json(connection_id, {
                            "type": "token",
                            "content": token,
                        })
                        if not sent:
                            # Client disconnected mid-stream
                            break

                    # Signal end of stream
                    await manager.send_json(connection_id, {
                        "type": "done",
                        "content": "",
                    })

                except Exception as exc:
                    logger.error(f"[WS:{connection_id[:8]}] Generation error: {exc}")
                    await manager.send_json(connection_id, {
                        "type": "error",
                        "content": f"Generation failed: {str(exc)}",
                    })

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {connection_id[:8]}")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error for {connection_id[:8]}: {exc}")
    finally:
        manager.disconnect(connection_id)
