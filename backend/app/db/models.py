from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, DateTime, ForeignKey, Float, Float
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

    # Vision support: a model is multimodal only once its paired mmproj (CLIP
    # vision tower) file has also finished downloading — see llm_manager.py's
    # MTMDChatHandler wiring, which needs mmproj_path to build a chat_handler.
    is_vision = Column(Boolean, default=False)
    mmproj_filename = Column(String, nullable=True)
    mmproj_path = Column(String, nullable=True)
    mmproj_status = Column(String, nullable=True)  # 'downloading', 'downloaded', 'failed'

    # Vision support: a model is multimodal only once its paired mmproj (CLIP
    # vision tower) file has also finished downloading — see llm_manager.py's
    # MTMDChatHandler wiring, which needs mmproj_path to build a chat_handler.
    is_vision = Column(Boolean, default=False)
    mmproj_filename = Column(String, nullable=True)
    mmproj_path = Column(String, nullable=True)
    mmproj_status = Column(String, nullable=True)  # 'downloading', 'downloaded', 'failed'


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
    # True when upload-time OCR was skipped because a vision model was
    # active then (see api/documents.py's skip_ocr) — lets
    # ChatAgent._get_document_context (chat.py) notice, at send time, that
    # this image has NO searchable content at all if the active model is no
    # longer vision-capable by the time the message is actually sent, rather
    # than silently answering as if the image were never attached.
    ocr_skipped_for_vision = Column(Boolean, default=False)
    # True when upload-time OCR was skipped because a vision model was
    # active then (see api/documents.py's skip_ocr) — lets
    # ChatAgent._get_document_context (chat.py) notice, at send time, that
    # this image has NO searchable content at all if the active model is no
    # longer vision-capable by the time the message is actually sent, rather
    # than silently answering as if the image were never attached.
    ocr_skipped_for_vision = Column(Boolean, default=False)
    # SHA-256 of the raw uploaded bytes — lets a re-upload of the exact same
    # file within the same conversation reuse the existing row/embeddings
    # instead of re-ingesting a duplicate (see api/documents.py's upload
    # endpoint). Not unique across the whole table: the same content can
    # legitimately exist once per conversation, just not more than once
    # within one.
    content_hash = Column(String, index=True, nullable=True)

class Workflow(Base):
    """
    A user-designed workflow graph (n8n-style canvas) — replaces Agent Mode's
    LLM-driven tool selection: the user wires which tool runs at each step by
    hand, so there's no "guess the right tool from a menu" step for a local
    model to hallucinate on. graph_json stores React Flow's own {nodes, edges}
    shape verbatim (no backend-side translation layer) — see
    app.core.workflows.engine for how it's interpreted at run time.
    """
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    graph_json = Column(Text, nullable=False)  # JSON: {"nodes": [...], "edges": [...]}
    # At most one workflow may have this set — it becomes the live handler
    # for real chat messages (see app.core.workflows.engine.run_chat_workflow
    # and the /set-chat-handler, /unset-chat-handler endpoints in
    # app.api.workflows). False for every workflow = chat uses the built-in
    # ChatAgent pipeline exactly as before this feature existed.
    is_chat_handler = Column(Boolean, default=False, nullable=False)
    # Same idea as is_chat_handler, but for document uploads — at most one
    # workflow may have this set; it becomes the live handler run by
    # app.core.workflows.engine.run_ingestion_workflow in place of the
    # built-in app.core.rag.processor.ingest_document for every future
    # upload (see app.api.documents's upload handler and the
    # /set-ingestion-handler, /unset-ingestion-handler endpoints).
    is_ingestion_handler = Column(Boolean, default=False, nullable=False)
    # Null for a user-authored workflow. Set to app.core.workflows.seed's
    # SEED_VERSION for the built-in "Aegis Default Chat Pipeline" — lets
    # startup detect an old copy of that seed still sitting in an existing
    # install and overwrite it with the current shape (see seed.py) instead
    # of silently leaving a stale demo behind forever just because a row
    # with that name already exists.
    seed_version = Column(Integer, nullable=True)
    # Null for a user-authored workflow. A stable, never-shown identifier
    # for one of seed.py's built-in demo workflows (e.g.
    # "default_chat_pipeline") — distinct from `name`, which is a plain
    # editable text field on the canvas toolbar. Looking a seed row up by
    # `name` alone breaks the moment a user renames it (even temporarily,
    # then back): the next startup's by-name lookup misses the renamed row
    # and inserts a fresh duplicate under the canonical name, permanently
    # orphaning the original. seed_key lookups survive that; see seed.py's
    # module docstring for the one-time adoption/cleanup of any duplicate
    # this already caused on an existing install.
    seed_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowRun(Base):
    """
    A durable record of one execution of a Workflow — written by
    app.core.workflows.engine (run_workflow/run_chat_workflow/
    run_ingestion_workflow) so a run's outcome survives past the WebSocket
    broadcast that reported it live. Before this existed, a run's outputs
    only ever existed for the moment the workflow_run_complete/_failed
    message was on the wire — closing the canvas (or just not watching)
    lost them permanently, with nothing to debug a failure against.

    id is the same short run_id the engine already generates for its
    WebSocket messages (run_workflow's uuid4 hex, or a fresh one minted for
    a chat/ingestion run) — reusing it rather than a separate autoincrement
    key means a client that saw a live progress message can look this same
    run up afterwards with the id it already has.

    node_outputs_json is written once, at the end (success or failure) —
    intermediate per-node progress is still the WebSocket's job, this is
    purely the retrospective record. Values are sanitized first (see
    engine._summarize_output_for_history) so a run touching an embedding
    node doesn't write raw vectors into this table.
    """
    __tablename__ = "workflow_runs"

    id = Column(String, primary_key=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False, index=True)
    trigger = Column(String, nullable=False)  # "manual" | "chat" | "ingestion"
    status = Column(String, nullable=False, default="running")  # "running" | "completed" | "failed"
    failed_node_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    node_outputs_json = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class WorkflowVersion(Base):
    """
    A snapshot of a Workflow's graph_json taken right before it gets
    overwritten (see app.api.workflows.update_workflow) — lets a bad edit
    be undone without having to rebuild the graph by hand. Only the last
    WORKFLOW_VERSION_LIMIT (see app.api.workflows) rows per workflow_id are
    kept; older ones are pruned on insert so this can't grow unbounded on a
    workflow that's saved constantly while being edited.
    """
    __tablename__ = "workflow_versions"

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    graph_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class InstalledDatabase(Base):
    """
    A database or vector store the user installed from the Marketplace's
    Databases category, for use from a workflow's "database"/"vector" node
    (see app.core.workflows.engine and app.core.dbengines). engine_id names
    one entry in app.core.dbengines.registry's catalog (e.g. "sqlite",
    "duckdb", "qdrant", "lancedb", "chromadb") — sqlite/qdrant run in-process
    (already bundled), everything else runs through a portable `uv run
    --with <package>` subprocess downloaded on first use, same mechanism
    app.mcp.runtime_manager already uses for MCP servers.
    """
    __tablename__ = "installed_databases"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)              # user-given, shown in the workflow node picker
    engine_id = Column(String, nullable=False)          # see app.core.dbengines.registry
    category = Column(String, nullable=False)           # "relational" | "vector"
    storage_path = Column(String, nullable=False)       # file/dir under AEGIS_DATA_DIR/databases/<uuid>
    config_json = Column(Text, nullable=True)           # relational: {"schema": "<DDL>"} ; vector: {"embedding_model", "distance", "dim"}
    status = Column(String, default="installing")        # 'installing' | 'ready' | 'failed'
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # True only for the one auto-registered row representing Aegis's own
    # bundled hybrid document store (engine_id "aegis_hybrid" — see
    # app.core.workflows.engine's module docstring and
    # app.api.marketplace_databases, which refuses to let this be deleted).
    # False (the default) for everything a user installs themselves.
    is_builtin = Column(Boolean, default=False, nullable=False)


class EmbeddingModelRegistry(Base):
    """
    An embedding model downloaded from the Marketplace's Embedding Models
    category (app.api.marketplace_embeddings) — either one of fastembed's
    own supported dense text-embedding models (app.core.embeddings.registry,
    backend="fastembed") or an arbitrary Hugging Face repo the user typed in
    themselves, loaded via sentence-transformers instead since fastembed only
    runs models from its own fixed, ONNX-converted list (backend=
    "sentence_transformers"). Each gets its own cache_dir
    (AEGIS_DATA_DIR/embedding_models/<uuid>) rather than sharing either
    library's default cache, so deleting one is a plain rmtree with no
    HuggingFace cache-layout guessing. A workflow "vector" node picks one by
    model_id (data.embeddingModel) — see app.core.embeddings.manager.get_embedder,
    which branches on `backend` to know which library to load it with.
    """
    __tablename__ = "embedding_models"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, unique=True, nullable=False)   # fastembed catalog id, or a custom HF repo id
    display_name = Column(String, nullable=False)
    dim = Column(Integer, nullable=False)   # unknown for a custom model until its download finishes; 0 until then
    size_gb = Column(Float, nullable=True)
    cache_dir = Column(String, nullable=False)
    backend = Column(String, default="fastembed")  # 'fastembed' | 'sentence_transformers'
    status = Column(String, default="downloading")  # 'downloading' | 'downloaded' | 'failed'
    error_message = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RerankerModelRegistry(Base):
    """
    A cross-encoder reranker downloaded from the Marketplace's Rerankers
    category (app.api.marketplace_rerankers) — one of app.core.rerankers'
    curated CATALOG entries. Each gets its own cache_dir
    (AEGIS_DATA_DIR/reranker_models/<uuid>) rather than sharing
    sentence-transformers' default cache, so deleting one is a plain
    rmtree with no HuggingFace cache-layout guessing. A workflow
    "reranker" node picks one by model_id (data.rerankerModel) — see
    app.core.rerankers.get_cross_encoder.
    """
    __tablename__ = "reranker_models"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, unique=True, nullable=False)   # e.g. "BAAI/bge-reranker-base"
    display_name = Column(String, nullable=False)
    size_gb = Column(Float, nullable=True)
    cache_dir = Column(String, nullable=False)
    status = Column(String, default="downloading")  # 'downloading' | 'downloaded' | 'failed'
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
    # JSON array of {id, content, filename, document_id} — the RAG chunks
    # actually used to answer THIS assistant turn (null when none were
    # used). Lets a later turn's empty/weak search backfill from these
    # instead of leaving a vague follow-up ("what about the other part?")
    # with no grounding at all — see ChatAgent._backfill_sources_from_history.
    rag_sources_json = Column(Text, nullable=True)
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
    Per-conversation OFF-toggles for installed tools — the '+' menu's Tools
    switches (Claude Desktop-style: installed once globally via the
    Marketplace, then turned on/off per chat).

    Absence of a row means active (the default once installed) — only
    explicit "turned it off in this chat" state gets a row, so a newly
    installed capability is immediately usable everywhere without needing a
    row inserted for every conversation up front.
    """
    __tablename__ = "conversation_disabled_capabilities"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String, index=True, nullable=False)
    capability_type = Column(String, nullable=False)  # 'tool'
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


class UserCustomTable(Base):
    """
    Tracks a table a user created themselves through the "Aegis Database"
    workflow node's table browser (see app.core.aegis_db_browser) —
    distinct from every real Aegis table (User, Workflow, ChatMessage,
    ...), which are never rows here. Existing as its own registry (rather
    than inferring "user table" from some naming convention) is what lets
    aegis_db_browser.drop_table refuse to ever drop anything but a table a
    user genuinely created, and lets create_table check a name against
    every real table (Base.metadata.tables) AND this registry before
    ever running CREATE TABLE, so a user table can never collide with or
    shadow Aegis's own schema.
    """
    __tablename__ = "user_custom_tables"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    columns_json = Column(Text, nullable=False)  # JSON: [{"name": ..., "type": "text"|"integer"|"real"|"boolean", "nullable": bool}, ...]
    created_at = Column(DateTime, default=datetime.utcnow)
