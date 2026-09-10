"""
Aegis — WebSocket Endpoint (/ws)

Handles the persistent WebSocket connection from the Next.js frontend for
Chat Mode. Workflow runs (app.core.workflows.engine) broadcast their own
progress over the same connection manager but don't go through this
request/response loop — see app/api/workflows.py.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict

import anyio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.core.connection_manager import manager
from app.core.agents import ChatAgent

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
    """Main WebSocket endpoint for Chat Mode's message loop."""
    connection_id = client_id
    await manager.connect(websocket, connection_id)
    
    # Initialize an AgentSession for this client if it doesn't exist
    if connection_id not in agent_sessions:
        agent_sessions[connection_id] = ChatAgent(connection_id)
    session = agent_sessions[connection_id]

    try:
        # Send connected handshake — ignore failure if client already dropped (React Strict Mode)
        try:
            await websocket.send_json({
                "type": "connected",
                "connection_id": connection_id,
            })
        except Exception:
            # Client disconnected before we could say hello — clean up and exit
            manager.disconnect(connection_id, websocket)
            return

        # Load history from DB in a thread so we don't block the event loop,
        # then push it to the client as a dedicated "history" event. Uses the
        # attachments-including variant (not session._get_history(), which
        # feeds the LLM and deliberately omits them) so uploaded documents
        # render as attachment chips when a conversation reloads.
        try:
            def _load_history_with_attachments():
                from app.db.database import SessionLocal
                from app.db.crud import get_chat_history_with_attachments
                db = SessionLocal()
                try:
                    return get_chat_history_with_attachments(db, connection_id)
                finally:
                    db.close()

            full_history = await anyio.to_thread.run_sync(_load_history_with_attachments)
            if full_history:
                await websocket.send_json({
                    "type": "history",
                    "history": full_history,
                })
        except Exception as e:
            logger.warning(f"Failed to load/send history for {connection_id}: {e}")

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
            attachments = payload.get("attachments") or None
            export_format = payload.get("export_format") or None
            attachments = payload.get("attachments") or None
            export_format = payload.get("export_format") or None

            if msg_type == "ping":
                await manager.send_json(connection_id, {"type": "pong"})
                continue

            if msg_type == "cancel":
                logger.info(f"[WS:{connection_id[:8]}] Cancel signal received.")
                if hasattr(session, "cancel_event"):
                    session.cancel_event.set()
                # Unstick the UI immediately rather than waiting for the
                # in-flight task to notice. It often can't notice in time:
                # llama.cpp's prefill (producing the very first token) is one
                # uninterruptible blocking C call, so a turn stuck in
                # "Thinking…" — no tokens sent yet — has nowhere for
                # cancel_event to even be checked until that call returns on
                # its own. When it eventually does, the generation id check
                # in process_message_task below recognizes it's stale and
                # discards it instead of sending it to the client.
                if getattr(session, "is_processing", False):
                    session.is_processing = False
                    await manager.send_json(connection_id, {"type": "done", "content": ""})
                continue

            # ── Handle User Message ───────────────────────────────────────────
            # Allow attachment-only sends (no typed text) — Claude-style: a
            # user can just attach a file and hit send with nothing typed.
            if msg_type == "message" and (content.strip() or attachments):
                logger.info(f"[WS:{connection_id[:8]}] User: {content[:80]!r} attachments={len(attachments) if attachments else 0}")

                if getattr(session, 'is_processing', False):
                    await manager.send_json(connection_id, {
                        "type": "toast",
                        "content": "Aegis is currently busy processing another request."
                    })
                    continue

                # Claimed synchronously, right here in the receive loop — NOT
                # inside process_message_task below. asyncio.create_task()
                # only schedules that coroutine to start on a future event
                # loop tick, so if these were set at the top of the task body
                # instead, a "cancel" sent immediately after "message" could
                # reach this same receive loop and be processed first,
                # finding is_processing still False and doing nothing — then
                # the task starts a moment later and clobbers cancel_event
                # right back to unset, silently swallowing the cancel.
                # Claiming the turn here, before create_task even runs,
                # closes that window.
                session.is_processing = True
                session.generation_id += 1
                my_generation_id = session.generation_id
                if hasattr(session, "cancel_event"):
                    session.cancel_event.clear()

                async def process_message_task(msg_content: str, msg_attachments=attachments, msg_export_format=export_format):
                    def superseded() -> bool:
                        # True once a cancel (or a newer message) has moved
                        # the session on from this task — its eventual
                        # result, whenever the blocked call underneath it
                        # returns, should be discarded rather than sent.
                        return session.generation_id != my_generation_id

                    try:
                        streamed = False
                        loop = asyncio.get_running_loop()
                        token_queue = asyncio.Queue()

                        def send_token_sync(token: str):
                            nonlocal streamed
                            if superseded():
                                return
                            streamed = True
                            loop.call_soon_threadsafe(token_queue.put_nowait, token)

                        async def token_sender_loop():
                            while True:
                                token = await token_queue.get()
                                if token is None:
                                    break
                                await manager.send_json(connection_id, {
                                    "type": "token",
                                    "content": token
                                })

                        sender_task = asyncio.create_task(token_sender_loop())

                        async def send_status(msg: str):
                            # Deliberately its own type, not "toast" — this
                            # fires several times per turn ("Analyzing
                            # request...", "Searching your documents...",
                            # "Generating...") and the frontend collapses it
                            # into a single transient line that updates in
                            # place. A real "toast" (e.g. "Plan successfully
                            # scheduled!") is a one-off the user should
                            # actually see, so it stays a separate type.
                            if superseded():
                                return
                            await manager.send_json(connection_id, {
                                "type": "status",
                                "content": msg
                            })

                        await send_status("Analyzing request...")

                        def _get_active_chat_workflow_id():
                            from app.db.database import SessionLocal
                            from app.db.crud import get_active_chat_workflow
                            db = SessionLocal()
                            try:
                                wf = get_active_chat_workflow(db)
                                return wf.id if wf else None
                            finally:
                                db.close()

                        active_workflow_id = await anyio.to_thread.run_sync(_get_active_chat_workflow_id)

                        if active_workflow_id is not None:
                            # A workflow is connected as the chat handler (see
                            # app.api.workflows's /set-chat-handler) — run it
                            # instead of the built-in pipeline for this turn.
                            # Persisting the user message / assistant reply
                            # ourselves here mirrors exactly what
                            # ChatAgent._handle_idle does internally, since
                            # run_chat_workflow bypasses it entirely.
                            from app.core.workflows.engine import run_chat_workflow, WorkflowError
                            await session._append_history("user", msg_content, attachments=msg_attachments)
                            turn_history = await session._get_history()
                            turn_history = turn_history[:-1] if turn_history else []
                            try:
                                response_text = await run_chat_workflow(
                                    session, active_workflow_id, msg_content, turn_history, msg_attachments,
                                    connection_id, send_token_sync, export_format=msg_export_format,
                                )
                                await session._append_history("assistant", response_text)
                            except WorkflowError as e:
                                response_text = f"⚠ {e}"
                                await session._append_history("assistant", response_text)
                        else:
                            response_text = await session.handle_message(
                                msg_content,
                                token_callback=send_token_sync,
                                status_callback=send_status,
                                attachments=msg_attachments,
                                export_format=msg_export_format,
                            )

                        # Stop the token sender task
                        loop.call_soon_threadsafe(token_queue.put_nowait, None)
                        await sender_task

                        if superseded():
                            # A cancel (or a newer message) already moved the
                            # session on while this call was blocked — the
                            # cancel handler already told the client "done";
                            # sending this now would resurrect a turn the
                            # user thinks they cancelled.
                            logger.info(f"[WS:{connection_id[:8]}] Discarding superseded generation {my_generation_id}.")
                            return

                        if response_text.startswith("__system_toast__:"):
                            toast_msg = response_text.split(":", 1)[1]
                            await manager.send_json(connection_id, {
                                "type": "toast",
                                "content": toast_msg
                            })
                        else:
                            # Send the response directly only if we didn't stream it token-by-token
                            if not streamed:
                                await manager.send_json(connection_id, {
                                    "type": "token",
                                    "content": response_text
                                })

                            # Signal end of stream
                            await manager.send_json(connection_id, {
                                "type": "done",
                                "content": "",
                            })

                    except Exception as e:
                        logger.error(f"Error processing message: {e}", exc_info=True)
                        if not superseded():
                            await manager.send_json(connection_id, {
                                "type": "error",
                                "content": "An internal error occurred while processing your request.",
                            })
                    finally:
                        # Only clear is_processing if this task is still the
                        # session's current one — a superseded task (a cancel,
                        # or the user sending a new message before this one's
                        # blocked call finally returned) means something else
                        # already owns that flag, and stomping it here could
                        # wrongly mark a legitimately-running newer turn as idle.
                        if not superseded():
                            session.is_processing = False

                asyncio.create_task(process_message_task(content))

            # ── Handle Memory Saving Paths ─────────────────────────────────────
            elif msg_type == "save_whole_message" and content.strip():
                logger.info(f"[WS:{connection_id[:8]}] Save Whole Message Triggered")
                try:
                    async for progress in session.save_whole_message(content):
                        await manager.send_json(connection_id, {
                            "type": "token",
                            "content": progress
                        })
                    await manager.send_json(connection_id, {"type": "done", "content": ""})
                except Exception as exc:
                    logger.error(f"[WS:{connection_id[:8]}] Save error: {exc}")
                    await manager.send_json(connection_id, {"type": "error", "content": str(exc)})

            elif msg_type == "extract_specific_facts" and content.strip():
                logger.info(f"[WS:{connection_id[:8]}] Extract Specific Facts Triggered")
                try:
                    async for progress in session.extract_specific_facts(payload):
                        await manager.send_json(connection_id, {
                            "type": "token",
                            "content": progress
                        })
                    await manager.send_json(connection_id, {"type": "done", "content": ""})
                except Exception as exc:
                    logger.error(f"[WS:{connection_id[:8]}] Extraction error: {exc}")
                    await manager.send_json(connection_id, {"type": "error", "content": str(exc)})

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {connection_id[:8]}")
    except RuntimeError as exc:
        if "WebSocket is not connected" in str(exc) or "Need to call \"accept\" first" in str(exc):
            # This happens if the client drops the connection immediately after connecting,
            # especially in React Strict Mode which double-mounts components.
            logger.info(f"[WS] Client disconnected abruptly: {connection_id[:8]}")
        else:
            logger.error(f"[WS] Unexpected RuntimeError for {connection_id[:8]}: {exc}")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error for {connection_id[:8]}: {exc}")
    finally:
        manager.disconnect(connection_id, websocket)
        # Only destroy the ChatAgent if this WebSocket is still the active connection.
        # If the client reconnected and a new socket has already taken over this session ID,
        # the old disconnect must NOT wipe the ChatAgent (which holds in-progress LLM state,
        # _last_tool_results, _turn_counter, and plan data for the new connection).
        current_ws = manager._connections.get(connection_id)
        if current_ws is None and connection_id in agent_sessions:
            del agent_sessions[connection_id]
            logger.info(f"[WS] ChatAgent destroyed for {connection_id[:8]} (no active socket remaining)")
        elif current_ws is not None:
            logger.info(f"[WS] Skipping ChatAgent destroy for {connection_id[:8]} — new socket already active")
