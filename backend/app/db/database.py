import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite database file path (stored in backend directory)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "aegis.db"
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

def get_db():
    """Dependency generator to yield a DB session for API routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
