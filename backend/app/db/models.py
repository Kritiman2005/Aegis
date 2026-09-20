from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, DateTime, ForeignKey, Float, Float, UniqueConstraint
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

    # Per-model context window cap override, in tokens — None = fall back to
    # the app-wide default (see llm_manager.resolve_effective_n_ctx). Each
    # model has its own real native context_length above, so one global cap
    # either wastes headroom on a small-context model or (before this
    # column existed) got silently applied to every model regardless of
    # what it could actually support — this makes the cap a per-model
    # choice, set from Memory Hub's per-model card.
    context_cap = Column(Integer, nullable=True)


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


class OAuthAppCredential(Base):
    """
    A user's own OAuth app (client_id/client_secret) for one connector.

    Aegis has no hosted OAuth broker — there is no Aegis-owned app shared
    across every install. Each user registers their own OAuth client with
    the provider (Google Cloud Console, Slack API, etc.) and pastes the two
    values in here via the Connectors panel before the "Connect" button can
    start a login. Google's four catalog entries (mail/drive/docs/sheets)
    share one row under service_name "google", since Google issues one
    client covering however many scopes are requested — no reason to make
    the user register four separate apps for one GCP project.
    """
    __tablename__ = "oauth_app_credentials"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, unique=True, nullable=False, index=True)
    client_id = Column(String, nullable=False)
    client_secret = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    # active then (see api/documents.py's upload handler). Not currently
    # read back anywhere — the workflow-based chat path
    # (_attach_workflow_vision_image) just silently skips an image its
    # node's picked model can't read, rather than surfacing an explicit
    # "this image changed vision-availability since upload" note the way
    # the old built-in chat pipeline used to.
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
    # True = this workflow is connected as A live chat handler — either the
    # one GLOBAL handler (chat_handler_conversation_id NULL, applies to
    # every conversation with no more specific match) or scoped to ONE
    # conversation (chat_handler_conversation_id set, from the chat_trigger
    # node's own data.conversationId — see app.api.workflows's
    # /set-chat-handler). At most one row may be the global handler, and at
    # most one row may be scoped to any given conversation_id, but a global
    # handler and any number of differently-scoped handlers may all be
    # active at once — app.db.crud.get_active_chat_workflow does the
    # scoped-first-then-global lookup for one incoming message. False for
    # every workflow = chat is off for that scope; there's no built-in
    # fallback pipeline anymore (see app.api.websocket).
    is_chat_handler = Column(Boolean, default=False, nullable=False)
    chat_handler_conversation_id = Column(String, nullable=True, index=True)
    # Same idea as is_chat_handler/chat_handler_conversation_id, but for
    # document uploads — the live handler run by
    # app.core.workflows.engine.run_ingestion_workflow, either globally or
    # scoped to uploads made within one conversation (from the
    # document_upload_trigger node's data.conversationId — see
    # app.api.documents's upload handler and the /set-ingestion-handler,
    # /unset-ingestion-handler endpoints in app.api.workflows). Unlike
    # is_chat_handler, there's no built-in fallback when this is false —
    # ingestion is workflow-only, so an upload with nothing connected is
    # rejected outright rather than falling back to any default pipeline.
    is_ingestion_handler = Column(Boolean, default=False, nullable=False)
    ingestion_handler_conversation_id = Column(String, nullable=True, index=True)
    # Null for a user-authored workflow. Set to app.core.workflows.seed's
    # SEED_VERSION for the built-in "Aegis Default Chat Pipeline" — lets
    # startup detect an old copy of that seed still sitting in an existing
    # install and overwrite it with the current shape (see seed.py) instead
    # of silently leaving a stale demo behind forever just because a row
    # with that name already exists.
    seed_version = Column(Integer, nullable=True)
    # Null for a user-authored workflow. A stable, never-shown identifier
    # for one of seed.py's built-in demo workflows (e.g.
    # "default_pipeline") — distinct from `name`, which is a plain
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


class WorkflowNodeState(Base):
    """
    Small persisted key-value slot scoped to one (workflow, node) pair —
    state a node needs to remember BETWEEN separate runs, which node_outputs
    (rebuilt fresh every run) can't hold. Two current uses:
      - A "schedule_trigger" node's own bookkeeping: key="next_run_at",
        value=an ISO timestamp, read/written by
        app.core.scheduler.SchedulerDaemon's workflow-trigger check.
      - A "logic" node using operator=="changed_since_last_run": key=
        "last_value", value=the stringified subject from the previous run
        that held — lets a poll-driven trigger (e.g. gmail_list_messages on
        a schedule) only let the rest of the chain run when something
        actually changed, instead of re-summarizing the same latest email
        every single firing. See engine.py's _run_logic_node.
    (workflow_id, node_id, key) is unique — one row per fact.
    """
    __tablename__ = "workflow_node_state"
    __table_args__ = (UniqueConstraint("workflow_id", "node_id", "key", name="uq_workflow_node_state"),)

    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False, index=True)
    node_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False)
    value = Column(Text, nullable=True)
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
    # 'tool_call' marks internal-only entries (e.g. execution-result summaries
    # kept for LLM memory) that should replay into the collapsed "Agent is
    # working" card on reload instead of a normal top-level chat bubble.
    # Null for ordinary user-visible messages.
    msg_type = Column(String, nullable=True)
    # JSON array of {id, content, filename, document_id} — the RAG chunks
    # actually used to answer THIS assistant turn. add_chat_message still
    # accepts rag_sources to populate this, but nothing in
    # app.core.workflows.engine's chat path passes one, so this is
    # effectively always null now — the old built-in pipeline's
    # empty-search backfill-from-recent-turns behavior isn't modeled as a
    # workflow node and doesn't currently run.
    rag_sources_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationMeta(Base):
    """
    Per-conversation metadata that isn't itself a chat message — currently
    just a user-set custom title (Sidebar's rename option). No row means no
    custom title yet: get_all_sessions falls back to its own auto-derived
    "first message" preview, exactly as it always has. Kept as its own
    table rather than a column on ChatMessage since a title belongs to the
    conversation as a whole, not to any one message in it.
    """
    __tablename__ = "conversation_meta"

    conversation_id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    # {format: [engine_id, ...]} of NON-default extraction engines the user
    # has opted into (see app.core.extraction_engines) — each format's own
    # default engine is always enabled and never stored here, so an empty
    # "{}" here correctly means "only the defaults are installed" out of
    # the box, matching every other Marketplace category's own default.
    extraction_engines_json = Column(Text, nullable=False, default="{}")
    # {"transcription_engine": <id>} — which app.core.media_engines
    # TRANSCRIPTION_ENGINES id the chat composer's mic button uses (see
    # app.api.voice). Unset/missing key = "whisper-small" (the bundled
    # default, unchanged behavior from before this setting existed) — same
    # fallback contract app.core.media_engines.resolve_engine_id already
    # gives every other caller, so an old row with no key here just works.
    voice_json = Column(Text, nullable=False, default="{}")
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
    connectors_synced_at = Column(DateTime, nullable=True)  # last successful RemoteConnector resync; see account_auth.py
    created_at = Column(DateTime, default=datetime.utcnow)


class RemoteConnector(Base):
    """
    Local mirror of the full public connector catalog on
    aegisaistudio.online — every row, not just ones this account picked
    (there's no per-connector selection; one subscription unlocks the whole
    catalog, gated at render time by AegisAccount.cached_plan — see
    catalog.py's remote_entry_to_catalog_dict). Distinct from the free,
    built-in ones in app/mcp/catalog.py's CONNECTORS_CATALOG. Synced via
    get_full_catalog() in app/auth/supabase_client.py, on the same
    schedule/trigger points AegisAccount.cached_plan already uses (see
    _resync_catalog_in_background in account_auth.py).

    Field shape mirrors a CONNECTORS_CATALOG entry so catalog.py can convert
    a row here straight into the same dict shape the frontend already
    renders — command/env_schema/input_schema are stored as JSON text for
    the same reason MCPServer.config_json is.

    `version` is compared against the incoming row on every sync — a bump
    means the connector's definition changed upstream. If it's currently
    connected (a matching MCPServer.status == "connected" row exists),
    needs_reconnect is set instead of silently rewriting a live server's
    config out from under it; otherwise the cached row updates in place.
    """
    __tablename__ = "remote_connectors"

    id = Column(String, primary_key=True)  # matches the website's connectors.id slug
    display_name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    icon = Column(String, nullable=False)
    auth_type = Column(String, nullable=False)
    command_json = Column(Text, nullable=True)
    env_schema_json = Column(Text, nullable=False, default="[]")
    input_schema_json = Column(Text, nullable=False, default="[]")
    oauth_service = Column(String, nullable=True)
    setup_guide = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    needs_reconnect = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime, default=datetime.utcnow)


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
