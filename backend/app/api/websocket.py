"""
Aegis — WebSocket Endpoint (/ws)

Handles the persistent WebSocket connection from the Next.js frontend.
Integrated with the Agentic MCP Workflow state machine.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.core.connection_manager import manager
from app.core.agents import ChatAgent, AgentState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])

# Store active agent sessions per connection
agent_sessions: Dict[str, ChatAgent] = {}

# ─── WebSocket Endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str = Query(default_factory=lambda: str(uuid.uuid4())),
) -> None:
    """Main WebSocket endpoint with Stateful Agent loop."""
    connection_id = client_id
    await manager.connect(websocket, connection_id)
    
    # Initialize an AgentSession for this client
    session = ChatAgent(connection_id)
    agent_sessions[connection_id] = session

    # Notify client of their assigned connection ID
    await manager.send_json(connection_id, {
        "type": "connected",
        "connection_id": connection_id,
    })

    try:
        while True:
            raw = await websocket.receive_text()

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

            if msg_type == "ping":
                await manager.send_json(connection_id, {"type": "pong"})
                continue

            # ── Handle User Message (Agent Workflow) ─────────────────────────
            if msg_type == "message" and content.strip():
                logger.info(f"[WS:{connection_id[:8]}] User: {content[:80]!r}")

                try:
                    # Send a "thinking" indicator so the user knows the LLM is working
                    await manager.send_json(connection_id, {
                        "type": "token",
                        "content": "\n⏳ Thinking...\n"
                    })

                    # Process the message through the state machine
                    response_text = await session.handle_message(content)

                    # Clear thinking line then print response on a fresh line
                    await manager.send_json(connection_id, {
                        "type": "token",
                        "content": "\r" + " " * 20 + "\r" + response_text
                    })

                    # Signal end of stream
                    await manager.send_json(connection_id, {
                        "type": "done",
                        "content": "",
                    })
                    
                    # 2. If the user said "proceed", the state machine moves to EXECUTING
                    if session.state == AgentState.EXECUTING:
                        # Stream the execution progress token by token
                        async for progress in session.execute_plan():
                            await manager.send_json(connection_id, {
                                "type": "token",
                                "content": progress
                            })

                        # End stream when execution finishes
                        # (state is now IDLE or WAITING_MEMORY_CONFIRMATION)
                        await manager.send_json(connection_id, {
                            "type": "done",
                            "content": "",
                        })

                except Exception as exc:
                    logger.error(f"[WS:{connection_id[:8]}] Workflow error: {exc}")
                    await manager.send_json(connection_id, {
                        "type": "error",
                        "content": f"Workflow failed: {str(exc)}",
                    })

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {connection_id[:8]}")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error for {connection_id[:8]}: {exc}")
    finally:
        manager.disconnect(connection_id)
        if connection_id in agent_sessions:
            del agent_sessions[connection_id]
