import json
import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from app.db.models import User, ModelRegistry, MCPServer, MCPTool, ChatMessage

logger = logging.getLogger(__name__)

# ─── Google OAuth & Credentials Persistence ──────────────────────────────────

def save_google_user_and_credentials(
    db: Session,
    credentials: Credentials,
    service_name: str,
    user_email: str = "user@aegis.local",
    full_name: Optional[str] = None,
    available_tools: Optional[List[dict]] = None
) -> User:
    """Saves or updates user, Google OAuth credentials, MCP server, and tools in SQLite."""
    # Serialize credentials to JSON string
    creds_json = credentials.to_json()

    # Find or create user
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        user = User(
            email=user_email,
            full_name=full_name or "Aegis User",
            auth_provider="google",
            oauth_credentials_json=creds_json
        )
        db.add(user)
    else:
        user.oauth_credentials_json = creds_json
        if full_name:
            user.full_name = full_name

    db.commit()
    db.refresh(user)

    # Sync MCPServer record for Google Workspace (or Mail/Drive)
    server = db.query(MCPServer).filter(
        MCPServer.user_id == user.id,
        MCPServer.name == service_name
    ).first()

    if not server:
        display = "Google Mail" if service_name == "google_mail" else "Google Drive"
        server = MCPServer(
            user_id=user.id,
            name=service_name,
            display_name=display,
            server_type="google_api",
            status="connected"
        )
        db.add(server)
        db.commit()
        db.refresh(server)
    else:
        server.status = "connected"
        db.commit()

    # Sync MCPTools
    if available_tools:
        for tool_def in available_tools:
            tool_name = tool_def.get("name")
            tool_record = db.query(MCPTool).filter(
                MCPTool.server_id == server.id,
                MCPTool.name == tool_name
            ).first()

            param_str = json.dumps(tool_def.get("parameters", {}))
            if not tool_record:
                tool_record = MCPTool(
                    server_id=server.id,
                    name=tool_name,
                    description=tool_def.get("description"),
                    parameters_json=param_str,
                    is_enabled=True
                )
                db.add(tool_record)
            else:
                tool_record.description = tool_def.get("description")
                tool_record.parameters_json = param_str
        db.commit()

    logger.info(f"Successfully saved credentials & synced tools for user: {user.email}")
    return user


def sync_mcp_server_and_tools(
    db: Session,
    server_name: str,
    server_type: str = "stdio_mcp",
    display_name: Optional[str] = None,
    tools: Optional[List[dict]] = None,
    user_email: str = "user@aegis.local",
    account_context: Optional[dict] = None,
    config_json: Optional[dict] = None,
) -> MCPServer:
    """
    Generic function to upsert any MCP server and its discovered tools into SQLite.
    `account_context` stores authenticated account metadata (e.g. GitHub username)
    visible to the planner agent via tools_str injection.
    """
    user = db.query(User).filter(User.email == user_email).first()
    user_id = user.id if user else 1

    server = db.query(MCPServer).filter(
        MCPServer.user_id == user_id,
        MCPServer.name == server_name
    ).first()

    if not server:
        server = MCPServer(
            user_id=user_id,
            name=server_name,
            display_name=display_name or server_name,
            server_type=server_type,
            status="connected",
            account_context_json=json.dumps(account_context) if account_context else None,
            config_json=json.dumps(config_json) if config_json else None,
        )
        db.add(server)
        db.commit()
        db.refresh(server)
    else:
        server.status = "connected"
        if display_name:
            server.display_name = display_name
        if account_context is not None:
            server.account_context_json = json.dumps(account_context)
        if config_json is not None:
            server.config_json = json.dumps(config_json)
        db.commit()

    if tools:
        for tool_def in tools:
            tool_name = tool_def.get("name")
            tool_record = db.query(MCPTool).filter(
                MCPTool.server_id == server.id,
                MCPTool.name == tool_name
            ).first()

            # Handle both MCP inputSchema and legacy parameters formats
            schema = tool_def.get("inputSchema") or tool_def.get("parameters") or {}
            param_str = json.dumps(schema)

            if not tool_record:
                tool_record = MCPTool(
                    server_id=server.id,
                    name=tool_name,
                    description=tool_def.get("description"),
                    parameters_json=param_str,
                    is_enabled=True
                )
                db.add(tool_record)
            else:
                tool_record.description = tool_def.get("description")
                tool_record.parameters_json = param_str
                tool_record.is_enabled = True
        db.commit()

    logger.info(f"Synced server '{server_name}' and {len(tools or [])} tools in SQLite.")
    return server


def set_mcp_server_status(db: Session, server_name: str, status: str = "disconnected"):
    """Updates the status of an MCP server in SQLite."""
    server = db.query(MCPServer).filter(MCPServer.name == server_name).first()
    if server:
        server.status = status
        db.commit()
        logger.info(f"Updated server '{server_name}' status to '{status}' in SQLite.")


def get_all_server_account_contexts(db: Session) -> dict:
    """
    Returns a merged dict of all connected servers' account_context_json.
    Used by the planner to inject authenticated account info (e.g. GitHub username)
    directly into the tool listing prompt so the LLM never guesses identifiers.
    
    Returns: {server_name: {key: value, ...}, ...}  (only servers with non-null context)
    """
    servers = db.query(MCPServer).filter(
        MCPServer.status == "connected",
        MCPServer.account_context_json.isnot(None)
    ).all()
    result = {}
    for s in servers:
        try:
            ctx = json.loads(s.account_context_json)
            if ctx:
                result[s.name] = ctx
        except Exception:
            pass
    return result



def get_active_google_credentials(db: Session, service_name: str) -> Optional[Credentials]:
    """Retrieves and reconstructs Google OAuth credentials if the service is connected."""
    server = db.query(MCPServer).filter(
        MCPServer.name == service_name,
        MCPServer.status == "connected"
    ).first()
    
    if not server:
        return None
        
    user = db.query(User).filter(User.id == server.user_id).first()
    if not user or not user.oauth_credentials_json:
        return None

    try:
        creds_data = json.loads(user.oauth_credentials_json)
        credentials = Credentials.from_authorized_user_info(creds_data)

        # Auto-refresh using refresh token if expired
        if credentials.expired and credentials.refresh_token:
            logger.info("Access token expired. Refreshing using saved refresh_token...")
            credentials.refresh(Request())
            # Save refreshed credentials back to SQLite
            user.oauth_credentials_json = credentials.to_json()
            db.commit()
            logger.info("Successfully auto-refreshed Google access token and updated SQLite.")

        return credentials
    except Exception as e:
        logger.error(f"Failed to load or refresh credentials from SQLite: {e}")
        return None


# ─── Model Registry Persistence ─────────────────────────────────────────────

def reconcile_model_registry(db: Session) -> int:
    """
    Delete any ModelRegistry row claiming status='downloaded' whose file no
    longer exists on disk. Run once at startup so a stale/orphaned row (e.g.
    from a removed model file, or manual disk cleanup) can never make the UI
    report a model as downloaded/active when it isn't really there. Returns
    the number of rows removed.
    """
    import os
    orphans = [
        m for m in db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").all()
        if not m.file_path or not os.path.exists(m.file_path)
    ]
    for m in orphans:
        logger.warning(f"Removing orphaned model registry row '{m.name}' — file not found at {m.file_path}")
        db.delete(m)
    if orphans:
        db.commit()
    return len(orphans)


def reconcile_stuck_documents(db: Session) -> int:
    """
    Mark any UserDocument still 'processing' as failed at startup. Ingestion
    runs as a background task inside the backend process — if the process
    gets killed or restarted mid-ingestion (an app restart, a crash), that
    row is orphaned permanently: no process will ever resume it, so its
    attachment chip in the chat would show a spinner forever with nothing
    coming (confirmed against two real rows stuck this way after a restart
    during testing). Returns the number of rows fixed.
    """
    from app.db.models import UserDocument
    stuck = db.query(UserDocument).filter(UserDocument.status == "processing").all()
    for d in stuck:
        d.status = "failed"
        d.error_message = "Processing was interrupted (app restarted) — please re-upload."
        logger.warning(f"Marking orphaned in-progress document '{d.filename}' (id={d.id}) as failed.")
    if stuck:
        db.commit()
    return len(stuck)


# ─── Conversation Entity Memory ──────────────────────────────────────────────

from app.db.models import ConversationEntity

def save_entity(
    db: Session,
    conversation_id: str,
    label: str,
    entity_type: str,
    entity_id: str,
    data: dict
) -> ConversationEntity:
    """
    Persists a user-confirmed entity to the conversation_entities table.
    Uses an UPSERT strategy based on conversation_id and (entity_id OR label).
    `data` is the full raw content (email body, file text, channel messages, etc.)
    """
    # Scope the match to the same entity_type — otherwise two different kinds of
    # entities that happen to share a label/id (e.g. an email thread and a Drive
    # file both labeled "Q3 report") collide and silently overwrite each other.
    existing_entity = db.query(ConversationEntity).filter(
        ConversationEntity.conversation_id == conversation_id,
        ConversationEntity.entity_type == entity_type,
        (ConversationEntity.entity_id == entity_id) |
        (func.lower(func.trim(ConversationEntity.label)) == label.strip().lower())
    ).first()

    data_json = json.dumps(data, ensure_ascii=False)

    if existing_entity:
        existing_entity.label = label
        existing_entity.entity_type = entity_type
        existing_entity.entity_id = entity_id
        existing_entity.data_json = data_json
        entity = existing_entity
        logger.info(f"Upserted (Updated) entity [{entity_type}] '{label}' for session {conversation_id[:8]}")
    else:
        entity = ConversationEntity(
            conversation_id=conversation_id,
            label=label,
            entity_type=entity_type,
            entity_id=entity_id,
            data_json=data_json
        )
        db.add(entity)
        logger.info(f"Upserted (Inserted) entity [{entity_type}] '{label}' for session {conversation_id[:8]}")

    db.commit()
    db.refresh(entity)
    return entity


def get_session_entities(db: Session, conversation_id: str) -> List[ConversationEntity]:
    """Returns all confirmed entities for a given conversation session."""
    return (
        db.query(ConversationEntity)
        .filter(ConversationEntity.conversation_id == conversation_id)
        .order_by(ConversationEntity.created_at)
        .all()
    )


def build_entity_context_block(db: Session, conversation_id: str) -> str:
    """
    Builds a compact, structured text block of confirmed session entities
    to be injected at the top of the LLM system prompt.
    Returns an empty string if no entities are confirmed yet.
    """
    entities = get_session_entities(db, conversation_id)
    if not entities:
        return ""

    lines = ["## Session Memory (confirmed by you):", ""]
    for e in entities:
        data = json.loads(e.data_json)
        lines.append(f"[{e.entity_type}] \"{e.label}\" (ID: {e.entity_id})")
        # Inline actual content so LLM reasons directly on real data
        for key, value in data.items():
            if isinstance(value, (str, int, float)):
                lines.append(f"  {key}: {str(value)[:400]}")
            elif isinstance(value, list):
                lines.append(f"  {key}: {json.dumps(value[:5])}")
        lines.append("")

    return "\n".join(lines)


def get_all_entities(db: Session) -> List[ConversationEntity]:
    """Returns all confirmed entities globally."""
    return db.query(ConversationEntity).order_by(ConversationEntity.created_at.desc()).all()


def delete_entity(db: Session, entity_id: int) -> bool:
    """Deletes an entity by ID."""
    entity = db.query(ConversationEntity).filter(ConversationEntity.id == entity_id).first()
    if entity:
        db.delete(entity)
        db.commit()
        return True
    return False


def update_entity(db: Session, entity_id: int, label: str = None, data_json: str = None) -> ConversationEntity:
    """Updates an entity's label or data_json."""
    entity = db.query(ConversationEntity).filter(ConversationEntity.id == entity_id).first()
    if entity:
        if label is not None:
            entity.label = label
        if data_json is not None:
            entity.data_json = data_json
        db.commit()
        db.refresh(entity)
    return entity


# ─── Chat History Persistence ────────────────────────────────────────────────

from app.db.models import ChatMessage

def add_chat_message(
    db: Session, conversation_id: str, role: str, content: str,
    attachments: Optional[list] = None, msg_type: Optional[str] = None,
    rag_sources: Optional[list] = None,
) -> ChatMessage:
    """Adds a new message to the persistent chat history. `attachments` is an
    optional list of {document_id, filename, file_type} dicts — set when this
    message represents an uploaded document, so it renders as an attachment
    chip in the transcript (see get_chat_history_with_attachments). `msg_type`
    of 'tool_call' marks internal-only entries that should replay into the
    collapsed "Agent is working" card instead of a normal chat bubble.
    `rag_sources` is an optional list of {id, content, filename, document_id}
    dicts — the document chunks actually used to answer THIS turn, if any,
    so a later turn's weak/empty search can backfill from them (see
    ChatAgent._backfill_sources_from_history)."""
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        attachments_json=json.dumps(attachments) if attachments else None,
        msg_type=msg_type,
        rag_sources_json=json.dumps(rag_sources) if rag_sources else None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg

def get_chat_history(db: Session, conversation_id: str) -> List[dict]:
    """Retrieves all chat messages for a given session, ordered by time.

    Returns ONLY role+content — this feeds directly into the LLM's messages
    list (create_chat_completion), so it deliberately excludes attachments:
    an extra key there risks breaking the chat-completion message schema.
    For the frontend's own history payload, use
    get_chat_history_with_attachments instead.
    """
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    # Return as standard dict array for LLM injection
    return [{"role": m.role, "content": m.content} for m in messages]

def get_chat_history_with_attachments(db: Session, conversation_id: str) -> List[dict]:
    """Like get_chat_history but includes each message's attachments (if
    any) — for the frontend's own history payload only. Never pass this to
    the LLM's messages list."""
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    result = []
    for m in messages:
        entry: dict = {"role": m.role, "content": m.content}
        if m.attachments_json:
            try:
                entry["attachments"] = json.loads(m.attachments_json)
            except Exception:
                pass
        if m.msg_type:
            entry["msg_type"] = m.msg_type
        result.append(entry)
    return result

def search_messages(db: Session, query: str, conversation_id: Optional[str] = None, limit: int = 30) -> List[dict]:
    """
    Full-text search over chat message content via chat_messages_fts (see
    app.db.database's init_db — internal-only tool-call bookkeeping entries
    are excluded at index time, not here, so they never surface as a
    result). Query terms are individually double-quoted before being sent
    to FTS5's MATCH so a raw user string can never be interpreted as FTS5
    query syntax (column filters, boolean operators, a leading '-') — just
    literal word matches, ANDed together.
    """
    terms = query.strip().split()
    if not terms:
        return []
    fts_query = " ".join('"' + t.replace('"', '""') + '"' for t in terms)

    sql = """
        SELECT cm.id, cm.conversation_id, cm.role, cm.created_at,
               snippet(chat_messages_fts, 0, '**', '**', '…', 12) AS snippet
        FROM chat_messages_fts
        JOIN chat_messages cm ON cm.id = chat_messages_fts.rowid
        WHERE chat_messages_fts MATCH :query
    """
    params: dict = {"query": fts_query, "limit": limit}
    if conversation_id:
        sql += " AND cm.conversation_id = :conversation_id"
        params["conversation_id"] = conversation_id
    sql += " ORDER BY rank LIMIT :limit"

    rows = db.execute(text(sql), params).fetchall()
    return [
        {
            "message_id": r.id,
            "conversation_id": r.conversation_id,
            "role": r.role,
            # A raw text() query bypasses the ORM's usual str->datetime
            # coercion for SQLite, so this is already the driver's plain
            # string value (SQLAlchemy's ISO8601-ish default) — not a
            # datetime object to call .isoformat() on.
            "created_at": r.created_at,
            "snippet": r.snippet,
        }
        for r in rows
    ]


def get_all_sessions(db: Session) -> List[dict]:
    """Retrieves all distinct chat sessions, with the first user message as a preview."""
    # Find the earliest message for each conversation
    subquery = db.query(
        ChatMessage.conversation_id,
        func.min(ChatMessage.created_at).label('first_message_time')
    ).group_by(ChatMessage.conversation_id).subquery()
    
    # Get the first message content (preferring 'user' role)
    sessions = []
    conversations = db.query(subquery.c.conversation_id, subquery.c.first_message_time).order_by(subquery.c.first_message_time.desc()).all()
    
    for conv_id, start_time in conversations:
        # Get message count
        msg_count = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count()
        
        # Get preview (first user message, or any first message)
        first_msg = db.query(ChatMessage).filter(
            ChatMessage.conversation_id == conv_id,
            ChatMessage.role == 'user'
        ).order_by(ChatMessage.created_at.asc()).first()
        
        if not first_msg:
            first_msg = db.query(ChatMessage).filter(
                ChatMessage.conversation_id == conv_id
            ).order_by(ChatMessage.created_at.asc()).first()
            
        preview = first_msg.content[:100] + "..." if first_msg and len(first_msg.content) > 100 else (first_msg.content if first_msg else "Empty session")
        
        sessions.append({
            "id": conv_id,
            "preview": preview,
            "message_count": msg_count,
            "created_at": start_time.isoformat() if start_time else None
        })
        
    return sessions

def delete_chat_session(db: Session, conversation_id: str) -> int:
    """Deletes all messages for a given session. Returns the number of rows deleted."""
    deleted = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


# ─── Configuration & Telemetry ───────────────────────────────────────────────

def get_system_settings(db: Session):
    from app.db.models import SystemSettings
    settings = db.query(SystemSettings).filter(SystemSettings.id == 1).first()
    if not settings:
        settings = SystemSettings(
            id=1,
            chat_json="{}",
            planner_json="{}",
            advanced_json="{}",
            hardware_json="{}"
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

def update_system_settings(
    db: Session,
    chat_json: Optional[str] = None,
    planner_json: Optional[str] = None,
    advanced_json: Optional[str] = None,
    hardware_json: Optional[str] = None
):
    settings = get_system_settings(db)
    if chat_json is not None:
        settings.chat_json = chat_json
    if planner_json is not None:
        settings.planner_json = planner_json
    if advanced_json is not None:
        settings.advanced_json = advanced_json
    if hardware_json is not None:
        settings.hardware_json = hardware_json
    
    db.commit()
    db.refresh(settings)
    return settings

def log_setting_change(db: Session, setting_path: str, old_value: str, new_value: str):
    from app.db.models import SettingsHistory
    # Add new record
    history_record = SettingsHistory(
        setting_path=setting_path,
        old_value=old_value,
        new_value=new_value
    )
    db.add(history_record)
    db.commit()
    
    # Enforce 1000-row cap with oldest-row eviction
    count = db.query(SettingsHistory).count()
    if count > 1000:
        # Find IDs of oldest rows to delete
        excess = count - 1000
        oldest_ids = db.query(SettingsHistory.id).order_by(SettingsHistory.changed_at.asc()).limit(excess).all()
        ids_to_delete = [r[0] for r in oldest_ids]
        if ids_to_delete:
            db.query(SettingsHistory).filter(SettingsHistory.id.in_(ids_to_delete)).delete(synchronize_session=False)
            db.commit()

def get_mcp_server_by_name(db: Session, server_name: str) -> Optional[MCPServer]:
    """Looks up a single saved MCP server row by name, connected or not — used by the
    generic reload path (app.mcp.registry.reconnect_from_saved_config) to re-read its
    stored config_json regardless of current in-memory connection state."""
    return db.query(MCPServer).filter(MCPServer.name == server_name).first()


def get_all_connected_servers(db: Session) -> List[MCPServer]:
    """Returns all servers marked as connected in the DB."""
    return db.query(MCPServer).filter(MCPServer.status == "connected").all()


# ─── Token Usage / Analytics ─────────────────────────────────────────────────

from app.db.models import TokenUsage
from datetime import datetime as _datetime, timedelta as _timedelta

def get_active_model_display_name(db: Session) -> str:
    """
    Resolve the currently active model's display name for usage logging.
    Mirrors the fallback logic in agents/base.py's get_llm(): prefer the
    explicitly active model, else the first downloaded one.
    """
    active = db.query(ModelRegistry).filter(
        ModelRegistry.status == "downloaded",
        ModelRegistry.is_active == True
    ).first()
    if not active:
        active = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").first()
    return active.display_name if active else "unknown"


def get_active_vision_mmproj_path(db: Session) -> Optional[str]:
    """
    Resolve the active model's mmproj (vision tower) path, if it's a vision
    model whose companion file has actually finished downloading. Mirrors
    get_active_model_display_name's own active-model fallback logic — shared
    by BaseAgent.get_active_vision_mmproj_path (agents deciding whether to
    attach an image as real vision input) and the documents upload endpoint
    (deciding whether to skip OCR for an image entirely, since vision will
    handle it instead).
    """
    import os
    active = db.query(ModelRegistry).filter(
        ModelRegistry.status == "downloaded",
        ModelRegistry.is_active == True
    ).first()
    if not active:
        active = db.query(ModelRegistry).filter(ModelRegistry.status == "downloaded").first()
    if not active or active.mmproj_status != "downloaded" or not active.mmproj_path:
        return None
    return active.mmproj_path if os.path.exists(active.mmproj_path) else None


def get_model_vision_mmproj_path(db: Session, model_name: str) -> Optional[str]:
    """
    Same as get_active_vision_mmproj_path, but for one SPECIFIC named model
    rather than whichever one is currently active for chat — a workflow
    "llm" node picks its own model explicitly (data.modelName), which may
    or may not be the active chat model, so vision input for a workflow run
    (see engine.py's _attach_workflow_vision_image) must check the model
    the node actually resolved, not the globally active one.
    """
    import os
    row = db.query(ModelRegistry).filter(
        ModelRegistry.status == "downloaded",
        ModelRegistry.name == model_name,
    ).first()
    if not row or row.mmproj_status != "downloaded" or not row.mmproj_path:
        return None
    return row.mmproj_path if os.path.exists(row.mmproj_path) else None


def log_token_usage(
    db: Session,
    conversation_id: Optional[str],
    model_name: str,
    source: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Record one LLM call's real token counts (source: 'chat' or 'agent')."""
    db.add(TokenUsage(
        conversation_id=conversation_id,
        model_name=model_name,
        source=source,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    ))
    db.commit()


def get_analytics_summary(db: Session) -> dict:
    """Aggregate totals for the Analytics page's headline stat cards."""
    row = db.query(
        func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
        func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
    ).first()
    prompt_total, completion_total = row[0], row[1]
    return {
        "tokens_generated": completion_total,
        "prompt_tokens_processed": prompt_total,
        "total_tokens": prompt_total + completion_total,
    }


def get_token_usage_daily(db: Session, days: int = 7) -> List[dict]:
    """Tokens generated per calendar day for the last N days, oldest first."""
    since = _datetime.utcnow() - _timedelta(days=days - 1)
    day_expr = func.date(TokenUsage.created_at)
    rows = (
        db.query(day_expr.label("day"), func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens))
        .filter(TokenUsage.created_at >= since)
        .group_by(day_expr)
        .order_by(day_expr)
        .all()
    )
    by_day = {r[0]: r[1] for r in rows}
    result = []
    for i in range(days):
        d = (since + _timedelta(days=i)).date()
        key = d.isoformat()
        result.append({"date": key, "tokens": by_day.get(key, 0)})
    return result


def get_token_usage_by_model(db: Session) -> List[dict]:
    """Total tokens (prompt + completion) grouped by model, most-used first."""
    rows = (
        db.query(TokenUsage.model_name, func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens))
        .group_by(TokenUsage.model_name)
        .order_by(func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens).desc())
        .all()
    )
    return [{"model": r[0], "tokens": r[1]} for r in rows]


def get_token_usage_by_source(db: Session) -> List[dict]:
    """Total tokens (prompt + completion) grouped by source ('chat' / 'agent')."""
    rows = (
        db.query(TokenUsage.source, func.sum(TokenUsage.prompt_tokens + TokenUsage.completion_tokens))
        .group_by(TokenUsage.source)
        .all()
    )
    return [{"source": r[0], "tokens": r[1]} for r in rows]


# ── Per-conversation Tool activation ────────────────────────────────────────
from app.db.models import ConversationDisabledCapability


def is_capability_active(db: Session, conversation_id: str, capability_type: str, capability_id: str) -> bool:
    """Active unless explicitly turned off for this conversation — see
    ConversationDisabledCapability's docstring for why absence means active."""
    row = db.query(ConversationDisabledCapability).filter(
        ConversationDisabledCapability.conversation_id == conversation_id,
        ConversationDisabledCapability.capability_type == capability_type,
        ConversationDisabledCapability.capability_id == capability_id,
    ).first()
    return row is None


def set_capability_active(db: Session, conversation_id: str, capability_type: str, capability_id: str, active: bool) -> None:
    row = db.query(ConversationDisabledCapability).filter(
        ConversationDisabledCapability.conversation_id == conversation_id,
        ConversationDisabledCapability.capability_type == capability_type,
        ConversationDisabledCapability.capability_id == capability_id,
    ).first()
    if active and row:
        db.delete(row)
        db.commit()
    elif not active and not row:
        db.add(ConversationDisabledCapability(
            conversation_id=conversation_id, capability_type=capability_type, capability_id=capability_id,
        ))
        db.commit()


# ── Chat-connected workflows ─────────────────────────────────────────────────
from app.db.models import Workflow


def get_active_chat_workflow(db: Session, conversation_id: Optional[str] = None) -> Optional[Workflow]:
    """The workflow (if any) that should handle a chat message in
    `conversation_id` — see app.core.workflows.engine.run_chat_workflow and
    the /set-chat-handler, /unset-chat-handler endpoints in
    app.api.workflows. A workflow scoped to exactly this conversation (its
    chat_trigger node's data.conversationId) wins if one exists; otherwise
    falls back to the one GLOBAL handler (chat_handler_conversation_id
    NULL), if any — matches how it always worked before per-conversation
    scoping existed. `conversation_id=None` skips straight to the global
    lookup (used wherever the caller genuinely has no conversation
    context)."""
    if conversation_id is not None:
        scoped = (
            db.query(Workflow)
            .filter(Workflow.is_chat_handler == True, Workflow.chat_handler_conversation_id == conversation_id)  # noqa: E712
            .first()
        )
        if scoped:
            return scoped
    return (
        db.query(Workflow)
        .filter(Workflow.is_chat_handler == True, Workflow.chat_handler_conversation_id.is_(None))  # noqa: E712
        .first()
    )


def get_active_ingestion_workflow(db: Session, conversation_id: Optional[str] = None) -> Optional[Workflow]:
    """Same scoped-then-global lookup as get_active_chat_workflow, for
    document uploads — see app.api.documents's upload handler and the
    /set-ingestion-handler, /unset-ingestion-handler endpoints."""
    if conversation_id is not None:
        scoped = (
            db.query(Workflow)
            .filter(Workflow.is_ingestion_handler == True, Workflow.ingestion_handler_conversation_id == conversation_id)  # noqa: E712
            .first()
        )
        if scoped:
            return scoped
    return (
        db.query(Workflow)
        .filter(Workflow.is_ingestion_handler == True, Workflow.ingestion_handler_conversation_id.is_(None))  # noqa: E712
        .first()
    )


# ─── Token Usage / Analytics ─────────────────────────────────────────────────

from app.db.models import TokenUsage
from datetime import datetime as _datetime, timedelta as _timedelta


# ── Per-conversation Tool activation ────────────────────────────────────────
from app.db.models import ConversationDisabledCapability
