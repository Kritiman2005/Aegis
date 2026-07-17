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
