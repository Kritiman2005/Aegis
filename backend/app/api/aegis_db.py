"""
Aegis — Aegis Database browser API (/api/aegis-db)

Thin REST wrapper over app.core.aegis_db_browser — every handler's only
real job is translating that module's own TableError into
HTTPException(400), so the actual safety logic (allowlist, name-collision
checks, parameterized queries) lives in exactly one place. Powers the
"Aegis Database" workflow node's canvas UI (a Supabase-Table-Editor-style
browser for Aegis's own SQLite data), not a generic Marketplace-installed
database — see that module's docstring for the full safety model.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core import aegis_db_browser as browser

router = APIRouter(prefix="/api/aegis-db", tags=["Aegis Database"])


@router.get("/tables")
def list_tables(db: Session = Depends(get_db)):
    return {"tables": browser.list_tables(db)}


@router.get("/tables/{table}/rows")
def get_rows(table: str, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    try:
        return browser.get_rows(db, table, limit, offset)
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))


class InsertRowPayload(BaseModel):
    values: Dict[str, Any]


@router.post("/tables/{table}/rows")
def insert_row(table: str, payload: InsertRowPayload, db: Session = Depends(get_db)):
    try:
        return browser.insert_row(db, table, payload.values)
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))


class UpdateRowPayload(BaseModel):
    changes: Dict[str, Any]


@router.put("/tables/{table}/rows/{pk}")
def update_row(table: str, pk: str, payload: UpdateRowPayload, db: Session = Depends(get_db)):
    try:
        return browser.update_row(db, table, pk, payload.changes)
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tables/{table}/rows/{pk}")
def delete_row(table: str, pk: str, db: Session = Depends(get_db)):
    try:
        return browser.delete_row(db, table, pk)
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ColumnDef(BaseModel):
    name: str
    type: str
    nullable: bool = True


class CreateTablePayload(BaseModel):
    name: str
    columns: List[ColumnDef]


@router.post("/tables")
def create_table(payload: CreateTablePayload, db: Session = Depends(get_db)):
    try:
        return browser.create_table(db, payload.name, [c.model_dump() for c in payload.columns])
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tables/{table}")
def drop_table(table: str, db: Session = Depends(get_db)):
    try:
        return browser.drop_table(db, table)
    except browser.TableError as e:
        raise HTTPException(status_code=400, detail=str(e))
