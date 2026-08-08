import json
import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from app.db.models import User, ModelRegistry, MCPServer, MCPTool

logger = logging.getLogger(__name__)

# ─── Google OAuth & Credentials Persistence ──────────────────────────────────

def save_google_user_and_credentials(
    db: Session,
    credentials: Credentials,
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

    # Sync MCPServer record for Google Workspace
    server = db.query(MCPServer).filter(
        MCPServer.user_id == user.id,
        MCPServer.name == "google_workspace"
    ).first()

    if not server:
        server = MCPServer(
            user_id=user.id,
            name="google_workspace",
            display_name="Google Drive & Gmail Tools",
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
    user_email: str = "user@aegis.local"
) -> MCPServer:
    """Generic function to upsert any MCP server and its discovered tools into SQLite."""
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
            status="connected"
        )
        db.add(server)
        db.commit()
        db.refresh(server)
    else:
        server.status = "connected"
        if display_name:
            server.display_name = display_name
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



def get_active_google_credentials(db: Session) -> Optional[Credentials]:
    """
    Retrieves saved Google OAuth credentials from SQLite.
    If the access token is expired and a refresh token exists, it auto-refreshes
    with Google and updates SQLite transparently!
    """
    user = db.query(User).filter(User.oauth_credentials_json.isnot(None)).order_by(User.updated_at.desc()).first()
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

def seed_default_model(db: Session, model_path: str):
    """Ensures default local Qwen 2.5 3B model is registered in the models table."""
    existing = db.query(ModelRegistry).filter(ModelRegistry.name == "gemma-local").first()
    if not existing:
        model = ModelRegistry(
            name="gemma-local",
            display_name="Qwen 2.5 3B Instruct (Local GGUF)",
            repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF",
            filename="qwen2.5-3b-instruct-q4_k_m.gguf",
            file_path=model_path,
            status="downloaded",
            chat_format="chatml",
            context_length=4096,
            is_active=True
        )
        db.add(model)
        db.commit()
        logger.info("Registered default Qwen model in SQLite models table.")


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
    existing_entity = db.query(ConversationEntity).filter(
        ConversationEntity.conversation_id == conversation_id,
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

def add_chat_message(db: Session, conversation_id: str, role: str, content: str) -> ChatMessage:
    """Adds a new message to the persistent chat history."""
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg

def get_chat_history(db: Session, conversation_id: str) -> List[dict]:
    """Retrieves all chat messages for a given session, ordered by time."""
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    
    # Return as standard dict array for LLM injection
    return [{"role": m.role, "content": m.content} for m in messages]

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
            extractor_json="{}",
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
    extractor_json: Optional[str] = None,
    advanced_json: Optional[str] = None,
    hardware_json: Optional[str] = None
):
    settings = get_system_settings(db)
    if chat_json is not None:
        settings.chat_json = chat_json
    if planner_json is not None:
        settings.planner_json = planner_json
    if extractor_json is not None:
        settings.extractor_json = extractor_json
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
