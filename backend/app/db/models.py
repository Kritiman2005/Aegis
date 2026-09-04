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
    # JSON array of {document_id, filename, file_type} — set when this message
    # represents (or includes) an uploaded document, so it renders as an
    # attachment chip in the transcript instead of living only in the
    # separate Files page. Null/empty for ordinary text messages.
    attachments_json = Column(Text, nullable=True)
    # 'tool_call' marks internal-only entries (e.g. execution-result summaries
    # kept for LLM memory) that should replay into the collapsed "Agent is
    # working" card on reload instead of a normal top-level chat bubble.
    # Null for ordinary user-visible messages.
    msg_type = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TokenUsage(Base):
    """
    One row per LLM call, recording real token counts (via the model's own
    tokenizer, not an estimate) for the Analytics page.
    """
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=True)
    model_name = Column(String, nullable=False)
    source = Column(String, nullable=False)  # 'chat' or 'agent'
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class ConversationDisabledCapability(Base):
    """
    Per-conversation OFF-toggles for installed tools/skills — the '+' menu's
    Tools/Skills switches (Claude Desktop-style: installed once globally via
    the Marketplace, then turned on/off per chat).

    Absence of a row means active (the default once installed) — only
    explicit "turned it off in this chat" state gets a row, so a newly
    installed capability is immediately usable everywhere without needing a
    row inserted for every conversation up front.
    """
    __tablename__ = "conversation_disabled_capabilities"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=False)
    capability_type = Column(String, nullable=False)  # 'tool' | 'skill'
    capability_id = Column(String, nullable=False)


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


class AegisAccount(Base):
    """
    The signed-in Aegis cloud account (Supabase-backed) — deliberately
    separate from the legacy `User` table above, which is really "Google
    OAuth credentials for the connector system," not an app-account concept.
    Single-row, same convention as SystemSettings: this is a single-user
    desktop app, so there is at most one signed-in account at a time.
    Only the long-lived refresh_token is persisted — the short-lived access
    token is kept in memory and re-derived on demand (see account_auth.py).
    refresh_token itself normally holds token_store.KEYCHAIN_SENTINEL, not
    the real value — that lives in the OS keychain (macOS Keychain / Windows
    Credential Manager / Linux Secret Service) instead, so a plaintext copy
    of a live bearer credential isn't sitting in this SQLite file. Falls
    back to storing the real value directly here only if the OS keychain is
    ever unavailable — see app/auth/token_store.py.
    """
    __tablename__ = "aegis_account"

    id = Column(Integer, primary_key=True, default=1)
    supabase_user_id = Column(String, nullable=False)
    email = Column(String, nullable=False)
    refresh_token = Column(Text, nullable=False)
    cached_plan = Column(String, default="free")
    plan_synced_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class OnboardingState(Base):
    """
    Single-row table (same convention as SystemSettings/AegisAccount) tracking
    one-time onboarding moments — right now just the "Your Mac is ready"
    hardware-detection welcome screen, shown once ever, not once per launch.
    """
    __tablename__ = "onboarding_state"

    id = Column(Integer, primary_key=True, default=1)
    welcome_seen = Column(Boolean, default=False)
