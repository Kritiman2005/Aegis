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
