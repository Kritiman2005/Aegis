import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Data directory for persistent storage
BASE_DIR = Path(__file__).resolve().parent.parent.parent
data_dir = os.environ.get("AEGIS_DATA_DIR")
if data_dir:
    DB_DIR = Path(data_dir)
else:
    DB_DIR = BASE_DIR
    
DB_PATH = DB_DIR / "aegis.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Create SQLite Engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # Needed for SQLite in multithreaded FastAPI
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative Base for ORM Models
Base = declarative_base()

def init_db():
    """Create all tables defined in models.py if they don't exist yet."""
    import app.db.models  # Ensures models are registered with Base
    Base.metadata.create_all(bind=engine)

    # Base.metadata.create_all only creates tables that don't exist yet — it
    # never alters an existing table to add a newly-introduced column. A
    # persistent database from before ChatMessage.attachments_json was added
    # would otherwise fail on every single insert ("no such column:
    # attachments_json"), breaking ordinary chat messages and uploads alike,
    # not just the new attachment feature (confirmed against a real installed
    # database that predated the column). Add any missing columns here by
    # hand, defensively — not a real migration system, just enough to keep
    # existing installs working across a schema change like this one.
    from sqlalchemy import text
    with engine.connect() as conn:
        existing_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(chat_messages)"))}
        if "attachments_json" not in existing_cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN attachments_json TEXT"))
            conn.commit()
        if "msg_type" not in existing_cols:
            # Lets internal-only history entries (e.g. the tool-results summary
            # kept purely for LLM memory) replay into the collapsed "Agent is
            # working" card on reload instead of a raw top-level bubble.
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN msg_type TEXT"))
            conn.commit()
        if "rag_sources_json" not in existing_cols:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN rag_sources_json TEXT"))
            conn.commit()
        if "msg_type" not in existing_cols:
            # Lets internal-only history entries (e.g. the tool-results summary
            # kept purely for LLM memory) replay into the collapsed "Agent is
            # working" card on reload instead of a raw top-level bubble.
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN msg_type TEXT"))
            conn.commit()

        existing_doc_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(user_documents)"))}
        if "ocr_skipped_for_vision" not in existing_doc_cols:
            conn.execute(text("ALTER TABLE user_documents ADD COLUMN ocr_skipped_for_vision BOOLEAN DEFAULT 0"))
            conn.commit()
        if "content_hash" not in existing_doc_cols:
            conn.execute(text("ALTER TABLE user_documents ADD COLUMN content_hash TEXT"))
            conn.commit()
        if "content_hash" not in existing_doc_cols:
            conn.execute(text("ALTER TABLE user_documents ADD COLUMN content_hash TEXT"))
            conn.commit()

        # Vision support (see ModelRegistry's own comment): an existing
        # install's models table predates these columns entirely.
        existing_model_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(models)"))}
        for col, ddl in (
            ("is_vision", "ALTER TABLE models ADD COLUMN is_vision BOOLEAN DEFAULT 0"),
            ("mmproj_filename", "ALTER TABLE models ADD COLUMN mmproj_filename TEXT"),
            ("mmproj_path", "ALTER TABLE models ADD COLUMN mmproj_path TEXT"),
            ("mmproj_status", "ALTER TABLE models ADD COLUMN mmproj_status TEXT"),
        ):
            if col not in existing_model_cols:
                conn.execute(text(ddl))
                conn.commit()

        existing_workflow_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(workflows)"))}
        if "is_chat_handler" not in existing_workflow_cols:
            conn.execute(text("ALTER TABLE workflows ADD COLUMN is_chat_handler BOOLEAN DEFAULT 0"))
            conn.commit()
        if "seed_version" not in existing_workflow_cols:
            conn.execute(text("ALTER TABLE workflows ADD COLUMN seed_version INTEGER"))
            conn.commit()
        if "is_ingestion_handler" not in existing_workflow_cols:
            conn.execute(text("ALTER TABLE workflows ADD COLUMN is_ingestion_handler BOOLEAN DEFAULT 0"))
            conn.commit()
        if "seed_key" not in existing_workflow_cols:
            conn.execute(text("ALTER TABLE workflows ADD COLUMN seed_key VARCHAR"))
            conn.commit()

        existing_installed_db_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(installed_databases)"))}
        if "is_builtin" not in existing_installed_db_cols:
            conn.execute(text("ALTER TABLE installed_databases ADD COLUMN is_builtin BOOLEAN DEFAULT 0"))
            conn.commit()


    # Setup FTS5 for MCP Tools
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS mcp_tools_fts USING fts5(
            name, 
            description, 
            content='mcp_tools', 
            content_rowid='id'
        );
        """))
        
        # Triggers to keep FTS table synchronized with mcp_tools
        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS mcp_tools_ai AFTER INSERT ON mcp_tools BEGIN
            INSERT INTO mcp_tools_fts(rowid, name, description) VALUES (new.id, new.name, new.description);
        END;
        """))
        
        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS mcp_tools_ad AFTER DELETE ON mcp_tools BEGIN
            INSERT INTO mcp_tools_fts(mcp_tools_fts, rowid, name, description) VALUES ('delete', old.id, old.name, old.description);
        END;
        """))
        
        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS mcp_tools_au AFTER UPDATE ON mcp_tools BEGIN
            INSERT INTO mcp_tools_fts(mcp_tools_fts, rowid, name, description) VALUES ('delete', old.id, old.name, old.description);
            INSERT INTO mcp_tools_fts(rowid, name, description) VALUES (new.id, new.name, new.description);
        END;
        """))
        
        # Force a rebuild on initialization to index any existing tools
        conn.execute(text("INSERT INTO mcp_tools_fts(mcp_tools_fts) VALUES('rebuild');"))

        # Setup FTS5 for chat message search — same external-content pattern
        # as mcp_tools_fts above. Insert triggers are guarded with
        # `WHEN new.msg_type IS NULL` so internal-only bookkeeping entries
        # (tool-call summaries kept for LLM memory, never shown as a normal
        # chat bubble — see ChatMessage.msg_type's docstring) never surface
        # in search results.
        conn.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
            content,
            content='chat_messages',
            content_rowid='id'
        );
        """))

        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages
        WHEN new.msg_type IS NULL BEGIN
            INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
        """))

        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
        END;
        """))

        conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
            INSERT INTO chat_messages_fts(rowid, content) SELECT new.id, new.content WHERE new.msg_type IS NULL;
        END;
        """))

        # Re-populate on every init rather than the 'rebuild' special command
        # mcp_tools_fts above uses — 'rebuild' re-scans the content table
        # directly and ignores trigger conditions, which would undo the
        # msg_type filter above for any pre-existing tool-call messages.
        conn.execute(text("DELETE FROM chat_messages_fts;"))
        conn.execute(text("INSERT INTO chat_messages_fts(rowid, content) SELECT id, content FROM chat_messages WHERE msg_type IS NULL;"))

        # Safe migration: add account_context_json to mcp_servers if it doesn't exist yet
        try:
            conn.execute(text(
                "ALTER TABLE mcp_servers ADD COLUMN account_context_json TEXT"
            ))
        except Exception:
            pass  # Column already exists — no action needed

        conn.commit()

def get_db():
    """Dependency generator to yield a DB session for API routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
