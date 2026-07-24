import json
import logging
from typing import Optional, List
from sqlalchemy.orm import Session
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
    `data` is the full raw content (email body, file text, channel messages, etc.)
    """
    entity = ConversationEntity(
        conversation_id=conversation_id,
        label=label,
        entity_type=entity_type,
        entity_id=entity_id,
        data_json=json.dumps(data, ensure_ascii=False)
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    logger.info(f"Saved entity [{entity_type}] '{label}' for session {conversation_id[:8]}")
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
