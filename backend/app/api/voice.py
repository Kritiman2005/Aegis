"""
Aegis — Voice Transcription API

Exposes app.core.transcription (faster-whisper) as a small set of endpoints:
check whether the bundled model is present, then transcribe a recorded clip
from the chat composer's mic button. The bundled "small" model ships inside
the app bundle (see app/core/transcription.py's module docstring) and is
always the guaranteed fallback — /status checks specifically that one, not
whichever engine is currently configured, so the frontend can hide the mic
button only on the rare broken/incomplete build where the bundle step
didn't run, never because a user-picked alternate engine isn't installed
yet (app.core.media_engines.resolve_engine_id already falls back to
"whisper-small" gracefully in that case).

Transcription itself is a plain request/response (not a background task +
websocket broadcast like document upload/scraping) — the composer is waiting
synchronously to drop the resulting text into the textarea, so there's
nothing to gain from making it fire-and-forget.

Which engine the mic actually uses is no longer a global preference — it's
configured on whichever workflow's "On chat message" (chat_trigger) node is
currently connected to a given conversation (see NodeData.voiceBackend/
voiceEngineId/voiceMcpTool/voiceMcpAudioField in WorkflowsView.tsx, and
_get_active_voice_config below), since different workflows may reasonably
want different transcription behavior. The mic button itself stays in the
chat composer (it's the only place you're actually talking during a live
conversation) but only records/sends — /active-config is what tells it
which engine or MCP tool to actually use for a given conversation.
"""

import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
import anyio

from app.core.transcription import is_installed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["Voice"])


_DEFAULT_VOICE_CONFIG = {
    # No workflow connected at all means chat runs the plain (non-workflow)
    # agent path, which has no acceptsVoice gate to fail — so voice is
    # accepted there too, same as a connected trigger's own default.
    "accepts_voice": True,
    "auto_send": False,
    "backend": "local",
    "engine_id": None,
    "mcp_tool": None,
    "mcp_audio_field": None,
}


def _get_active_voice_config(conversation_id: Optional[str]) -> dict:
    """Resolves voice transcription config from whichever workflow's
    chat_trigger node is currently connected to this conversation (or the
    global handler) — see app.db.crud.get_active_chat_workflow for the
    scoped-then-global lookup. No workflow connected, or its trigger has no
    voice config set, falls back to _DEFAULT_VOICE_CONFIG ("local engine,
    bundled default, voice accepted") — voice is accepted by default;
    engine.py's _run_chat_workflow_body rejects it only on an explicit
    `acceptsVoice: False` (same accepts-unless-turned-off convention
    mirrored here)."""
    from app.db.database import SessionLocal
    from app.db.crud import get_active_chat_workflow

    with SessionLocal() as db:
        workflow = get_active_chat_workflow(db, conversation_id)
        if not workflow:
            return dict(_DEFAULT_VOICE_CONFIG)
        try:
            graph = json.loads(workflow.graph_json)
        except (json.JSONDecodeError, TypeError):
            return dict(_DEFAULT_VOICE_CONFIG)

        trigger_data = None
        for n in graph.get("nodes", []):
            if n.get("data", {}).get("kind") == "chat_trigger":
                trigger_data = n.get("data", {})
                break
        if not trigger_data:
            return dict(_DEFAULT_VOICE_CONFIG)

        return {
            "accepts_voice": trigger_data.get("acceptsVoice") is not False,
            "auto_send": bool(trigger_data.get("voiceAutoSend")),
            "backend": trigger_data.get("voiceBackend") or "local",
            "engine_id": trigger_data.get("voiceEngineId") or None,
            "mcp_tool": trigger_data.get("voiceMcpTool") or None,
            "mcp_audio_field": trigger_data.get("voiceMcpAudioField") or None,
        }


def _run_mcp_transcription(file_path: str, tool_name: str, audio_field: str) -> str:
    """Calls a connected MCP server's tool with the recorded clip's bytes,
    base64-encoded into whichever input field the workflow author picked to
    receive it (see WorkflowsView's chat_trigger MCP picker — there's no
    standard MCP convention for "this argument accepts audio", so the field
    name is stored on the trigger node itself, not inferred). Base64 (not a
    file_path) works uniformly for local stdio AND remote HTTP MCP servers,
    unlike the file_path convention transcribe_media/extract_image_text use
    for same-machine local tools (app.core.agents.chat)."""
    import base64
    from app.mcp.registry import mcp_registry

    with open(file_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")

    raw = mcp_registry.call_tool(tool_name, {audio_field: audio_b64})
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            for key in ("text", "transcript", "transcription", "result"):
                value = parsed.get(key)
                if isinstance(value, str):
                    return value
        return raw
    return str(raw)


@router.get("/status")
def get_status():
    return {"status": "ready" if is_installed() else "unavailable"}


@router.get("/active-config")
def get_active_voice_config(conversation_id: Optional[str] = None):
    """What the chat composer's mic should do right now for this
    conversation: which trigger node (if any) is connected, whether it
    accepts voice at all, and — if so — whether to send automatically and
    which backend (a local engine or an MCP tool) performs the actual
    transcription. Called by the composer before it sends a recorded clip,
    so it always reflects whichever workflow is currently connected rather
    than a stale cached preference."""
    return _get_active_voice_config(conversation_id)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    partial: bool = Form(False),
):
    # Suffix matters: faster-whisper (via av/ffmpeg) picks its demuxer off the
    # file extension, and the composer always sends browser-recorded webm/opus.
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        config = _get_active_voice_config(conversation_id)
        # Live preview ticks (every ~1s while recording) always use a local
        # engine regardless of what's configured — round-tripping to an MCP
        # server that often would be slow/expensive/rate-limited. Only the
        # final "stop and transcribe" call honors an MCP-backed trigger.
        use_mcp = (
            not partial
            and config["backend"] == "mcp"
            and config["mcp_tool"]
            and config["mcp_audio_field"]
        )
        if use_mcp:
            text = await anyio.to_thread.run_sync(
                _run_mcp_transcription, tmp_path, config["mcp_tool"], config["mcp_audio_field"]
            )
        else:
            if not is_installed():
                raise HTTPException(status_code=409, detail="Voice model unavailable in this build.")
            from app.core.media_engines import run_transcription
            engine_id = config["engine_id"] if config["backend"] == "local" else None
            text = await anyio.to_thread.run_sync(run_transcription, tmp_path, engine_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail="Transcription failed.")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"text": text}
