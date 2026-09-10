"""
Aegis — Marketplace Databases API (/api/marketplace/databases)

Backs the Marketplace's Databases category: browse the engine catalog
(app.core.dbengines.registry), install a database or vector store from it
("download from the marketplace" — for a relational engine this means
handing over a table schema as config and letting the backend create the
file and run the migration directly, per the product ask; for a vector
engine it means picking how embeddings get stored), and list/delete what's
installed.

A workflow's "database"/"vector" node (app.core.workflows.engine)
references an installed row by id and calls app.core.dbengines.relational/
vector directly to run queries against it — this API is only the
create/list/delete surface, not the query path itself.
"""

import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.connection_manager import manager
from app.core.dbengines import relational, vector
from app.core.dbengines.registry import get_engine, list_engines
from app.db.database import SessionLocal, get_db
from app.db.models import InstalledDatabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/marketplace/databases", tags=["marketplace-databases"])

_data_dir = os.environ.get("AEGIS_DATA_DIR")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATABASES_DIR = Path(_data_dir) / "databases" if _data_dir else BASE_DIR / "databases"
DATABASES_DIR.mkdir(parents=True, exist_ok=True)


class InstallDatabaseRequest(BaseModel):
    name: str
    engine_id: str
    # Relational engines (sqlite, duckdb): user-supplied DDL, split on ';'
    # into statements and run as the install-time migration.
    schema_sql: Optional[str] = None
    # Vector engines (qdrant, lancedb, chromadb): how to store embeddings.
    embedding_dim: Optional[int] = None
    distance: Optional[str] = "cosine"  # "cosine" | "euclidean" | "dot"
    model_config = {"defer_build": True}


@router.get("/catalog")
def get_catalog():
    """Every engine the Databases category can offer, grouped client-side by
    `category`. `requires_download` tells the frontend whether picking this
    engine means a first-use `uv run --with <package>` download."""
    return {"engines": [
        {
            "id": e.id, "name": e.name, "category": e.category, "description": e.description,
            "requires_download": e.pip_package is not None, "placeholder_syntax": e.placeholder_syntax,
        }
        for e in list_engines()
    ]}


@router.get("")
def list_databases(db: Session = Depends(get_db)):
    rows = db.query(InstalledDatabase).order_by(InstalledDatabase.created_at.desc()).all()
    return {"databases": [_serialize(r) for r in rows]}


@router.delete("/{database_id}")
def delete_database(database_id: int, db: Session = Depends(get_db)):
    row = db.query(InstalledDatabase).filter(InstalledDatabase.id == database_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Database not found.")
    if row.is_builtin:
        raise HTTPException(status_code=400, detail="This is Aegis's own built-in document store — it can't be deleted.")

    path = Path(row.storage_path)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"Could not remove storage for database {database_id}: {e}")

    db.delete(row)
    db.commit()
    return {"message": f"Deleted '{row.name}'."}


async def _install_task(
    database_id: int, engine_id: str, storage_path: str,
    schema_sql: Optional[str], embedding_dim: Optional[int], distance: str,
) -> None:
    """Background task behind POST /install — the actual file creation plus
    migration (relational) or store creation (vector), broadcasting progress
    the same way MCP server connects and workflow runs already do (see
    app.api.connectors._connect_custom_task / app.core.workflows.engine)."""
    spec = get_engine(engine_id)

    async def broadcast(status: str, message: str) -> None:
        await manager.broadcast_json({
            "type": "database_install_progress", "database_id": database_id,
            "status": status, "message": message,
        })

    try:
        if spec.category == "relational":
            statements = [s.strip() for s in (schema_sql or "").split(";") if s.strip()]
            if not statements:
                raise ValueError("No table schema was provided.")
            await broadcast("running", f"Creating {spec.name} database and running migrations…")
            await relational.migrate(engine_id, storage_path, statements)
        else:
            if not embedding_dim:
                raise ValueError("An embedding dimension is required for a vector store.")
            await broadcast("running", f"Creating {spec.name} vector store…")
            await vector.create_store(engine_id, storage_path, embedding_dim, distance)

        with SessionLocal() as db:
            row = db.query(InstalledDatabase).filter(InstalledDatabase.id == database_id).first()
            if row:
                row.status = "ready"
                db.commit()
        await manager.broadcast_json({"type": "database_install_complete", "database_id": database_id})

    except Exception as e:
        logger.error(f"Database install {database_id} ({engine_id}) failed: {e}")
        with SessionLocal() as db:
            row = db.query(InstalledDatabase).filter(InstalledDatabase.id == database_id).first()
            if row:
                row.status = "failed"
                row.error_message = str(e)
                db.commit()
        await manager.broadcast_json({"type": "database_install_failed", "database_id": database_id, "message": str(e)})


@router.post("/install")
def install_database(req: InstallDatabaseRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    spec = get_engine(req.engine_id)
    if not spec:
        raise HTTPException(status_code=400, detail=f"Unknown engine '{req.engine_id}'.")

    instance_id = uuid.uuid4().hex[:12]
    if spec.category == "relational":
        ext = "sqlite" if req.engine_id == "sqlite" else "duckdb"
        storage_path = str(DATABASES_DIR / f"{instance_id}.{ext}")
        config: Dict[str, Any] = {"schema": req.schema_sql}
    else:
        storage_dir = DATABASES_DIR / instance_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = str(storage_dir)
        config = {"embedding_dim": req.embedding_dim, "distance": req.distance}

    row = InstalledDatabase(
        name=req.name, engine_id=req.engine_id, category=spec.category,
        storage_path=storage_path, config_json=json.dumps(config), status="installing",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background_tasks.add_task(
        _install_task, row.id, req.engine_id, storage_path, req.schema_sql, req.embedding_dim, req.distance or "cosine",
    )
    return _serialize(row)


def _serialize(row: InstalledDatabase) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "engine_id": row.engine_id,
        "category": row.category,
        "status": row.status,
        "error_message": row.error_message,
        "config": json.loads(row.config_json) if row.config_json else {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "is_builtin": bool(row.is_builtin),
    }
