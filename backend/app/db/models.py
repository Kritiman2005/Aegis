from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

class User(Base):
    """Stores user profile and authentication tokens."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    auth_provider = Column(String, default="google")  # 'google' or 'local'
    password_hash = Column(String, nullable=True)     # For local signups
    oauth_credentials_json = Column(Text, nullable=True)  # Stores serialized Google Credentials (tokens)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    mcp_servers = relationship("MCPServer", back_populates="user", cascade="all, delete-orphan")


class ModelRegistry(Base):
    """Tracks downloaded and available LLM models (HuggingFace / LMStudio style)."""
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)  # e.g., 'gemma-local'
    display_name = Column(String, nullable=False)                    # e.g., 'Qwen 2.5 3B Instruct'
    repo_id = Column(String, nullable=True)                         # HuggingFace repo ID
    filename = Column(String, nullable=True)                        # GGUF filename
    file_path = Column(String, nullable=False)                      # Path on disk
    file_size_bytes = Column(BigInteger, default=0)
    status = Column(String, default="available")                    # 'downloaded', 'downloading', 'available', 'failed'
    chat_format = Column(String, default="chatml")
    context_length = Column(Integer, default=4096)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class MCPServer(Base):
    """Tracks connected and disconnected MCP servers per user."""
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)          # e.g., 'google_workspace'
    display_name = Column(String, nullable=False)  # e.g., 'Google Drive & Gmail'
    server_type = Column(String, default="google_api")  # 'google_api', 'stdio_mcp', 'sse_mcp'
    status = Column(String, default="connected")   # 'connected', 'disconnected', 'error'
    config_json = Column(Text, nullable=True)      # JSON configuration
    account_context_json = Column(Text, nullable=True)  # JSON: authenticated account info shown to the planner
                                                        # e.g. {"authenticated_username": "Kritiman2005"}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="mcp_servers")
    tools = relationship("MCPTool", back_populates="server", cascade="all, delete-orphan")


class MCPTool(Base):
    """Stores individual tools provided by each connected MCP server."""
    __tablename__ = "mcp_tools"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("mcp_servers.id"), nullable=False)
    name = Column(String, nullable=False)          # e.g., 'gmail_read_message'
    description = Column(Text, nullable=True)
    parameters_json = Column(Text, nullable=True)  # JSON string of expected arguments schema
    is_enabled = Column(Boolean, default=True)

    # Relationships
    server = relationship("MCPServer", back_populates="tools")


class ConversationEntity(Base):
    """
    Universal session memory store.
    Holds any entity (email, file, channel, contact, etc.) the user explicitly
    confirms to remember within a chat session.
    All entity-type-specific fields live inside `data_json` as raw JSON.
    """
    __tablename__ = "conversation_entities"

    id              = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=False)  # WebSocket connection_id / session key
    label           = Column(String, nullable=False)              # Human-readable alias ("Instagram email", "#dev")
    entity_type     = Column(String, nullable=False)              # "gmail_message" | "drive_file" | "slack_channel" | "slack_message" | "contact" | ...
    entity_id       = Column(String, nullable=False)              # Actual ID in the external system
    data_json       = Column(Text, nullable=False)                # Full JSON content (email body, file text, channel messages, ...)
    created_at      = Column(DateTime, default=datetime.utcnow)

class UserDocument(Base):
    """
    Tracks files uploaded by the user for RAG/Chat Mode.
    """
    __tablename__ = "user_documents"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=True) # Optional link to a specific chat session
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=False) # e.g. pdf, pptx, txt, image
    status = Column(String, default="processing") # processing, ready, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
class ScheduledJob(Base):
    """
    Opt-in unattended scheduled jobs.
    Runs frozen plans with fresh arguments on a schedule.
    """
    __tablename__ = "scheduled_jobs"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=False)
    cron_expression = Column(String, nullable=False)             # e.g., '0 * * * *' (hourly) or a simple interval descriptor
    frozen_plan_json = Column(Text, nullable=False)              # JSON string of the plan array
    status = Column(String, default="active")                    # 'active', 'paused', 'missed', 'failed'
    next_run_at = Column(DateTime, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    """
    Persistent chat history for a session/conversation.
    Ensures that context survives server restarts and page reloads.
    """
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)                         # 'user', 'assistant', 'system'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class SystemSettings(Base):
    """
    Single-row table storing global configuration across Tiers A, B, and C.
    """
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    chat_json = Column(Text, nullable=False, default="{}")
    planner_json = Column(Text, nullable=False, default="{}")
    advanced_json = Column(Text, nullable=False, default="{}")
    hardware_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SettingsHistory(Base):
    """
    Telemetry log for tracking changes to load-bearing settings over time.
    """
    __tablename__ = "settings_history"

    id = Column(Integer, primary_key=True, index=True)
    setting_path = Column(String, index=True, nullable=False)  # e.g., 'advanced.rag_threshold'
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow)
