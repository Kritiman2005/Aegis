"""
Aegis — Safe browser/editor for Aegis's own SQLite database

Powers the "Aegis Database" workflow node (app.core.workflows.engine) and
its canvas UI (a Supabase-Table-Editor-style browser) — lets a user
inspect/edit their own app data (chat history, workflows, installed
databases, ...) and create genuinely new tables of their own, all inside
the same aegis.db SQLAlchemy already manages (app.db.database.engine), with
real guardrails:

  - Never exposes a table holding credentials/tokens (users, aegis_account,
    mcp_servers, mcp_tools — see each model's own docstring in
    app.db.models). SAFE_TABLES is a hand-reviewed ALLOWLIST, not a
    denylist, so a new sensitive table added to models.py in the future is
    excluded by default instead of accidentally exposed.
  - Every operation validates its table name against list_tables()'s own
    output first — an unlisted name (including any real table not on the
    allowlist) is rejected before it ever reaches SQL.
  - create_table rejects any name colliding with ANY real table
    (Base.metadata.tables, not just the safe list) or an existing user
    table; drop_table only ever accepts a name already registered in
    UserCustomTable — a real Aegis table can never be dropped this way.
  - Row values are always bound as query parameters, never string-
    formatted into SQL. Table/column identifiers ARE interpolated into
    the query text (SQL doesn't allow parameterizing identifiers), but
    only ever after being checked against a real, introspected column set
    or the validated allowlist/registry above — never raw user input.
"""

import json
import re
from typing import Any, Dict, List

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db.database import engine
from app.db.models import Base, UserCustomTable

# Every table in app.db.models NOT listed here is either a single-row
# bookkeeping table (SystemSettings, SettingsHistory, OnboardingState — no
# real "rows" concept, already editable through their own real surfaces)
# or holds credentials/tokens and is never exposed, in any form:
#   - users (oauth_credentials_json, password_hash)
#   - aegis_account (refresh_token)
#   - mcp_servers / mcp_tools (config_json/account_context_json can hold a
#     connected tool's own API keys/env vars)
SAFE_TABLES: Dict[str, str] = {
    "chat_messages": "Chat Messages",
    "workflows": "Workflows",
    "conversation_entities": "Memory / Entities",
    "user_documents": "Documents",
    "installed_databases": "Installed Databases",
    "embedding_models": "Embedding Models",
    "scheduled_jobs": "Scheduled Jobs",
    "token_usage": "Token Usage",
    "conversation_disabled_capabilities": "Disabled Capabilities",
    "models": "LLM Models",
}

_ALLOWED_COLUMN_TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL", "boolean": "BOOLEAN"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TableError(ValueError):
    """A request named a table/column/operation this module won't allow —
    always safe to surface to the user verbatim (never raw SQL/DB detail)."""


def _validate_identifier(name: str, kind: str = "name") -> None:
    if not _IDENTIFIER_RE.match(name or ""):
        raise TableError(f"'{name}' isn't a valid {kind} — use letters, numbers, and underscores, starting with a letter or underscore.")


def _pk_column(table_name: str) -> str:
    insp = inspect(engine)
    pks = insp.get_pk_constraint(table_name).get("constrained_columns") or []
    return pks[0] if pks else "id"


def _row_count(table_name: str) -> int:
    try:
        with engine.connect() as conn:
            return conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
    except Exception:
        return 0


def list_tables(db: Session) -> List[Dict[str, Any]]:
    """Every safe built-in table plus every user-created one, each with a
    live row count. The single source of truth for "is this table
    accessible at all" — every other function below checks its `table`
    argument against this list's own output first."""
    insp = inspect(engine)
    out: List[Dict[str, Any]] = []
    for name, label in SAFE_TABLES.items():
        columns = [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(name)]
        out.append({"name": name, "label": label, "columns": columns, "row_count": _row_count(name), "user_created": False})

    for row in db.query(UserCustomTable).order_by(UserCustomTable.created_at).all():
        columns = json.loads(row.columns_json)
        out.append({"name": row.name, "label": row.name, "columns": columns, "row_count": _row_count(row.name), "user_created": True})
    return out


def _assert_known_table(db: Session, table: str) -> Dict[str, Any]:
    for entry in list_tables(db):
        if entry["name"] == table:
            return entry
    raise TableError(f"'{table}' isn't a table this browser can access.")


def get_rows(db: Session, table: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    entry = _assert_known_table(db, table)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT * FROM "{table}" LIMIT :limit OFFSET :offset'),
            {"limit": limit, "offset": offset},
        ).mappings().all()
    return {"columns": entry["columns"], "rows": [dict(r) for r in rows], "row_count": entry["row_count"]}


def insert_row(db: Session, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
    entry = _assert_known_table(db, table)
    col_names = {c["name"] for c in entry["columns"]}
    values = {k: v for k, v in (values or {}).items() if k in col_names}
    if not values:
        raise TableError("No valid columns to insert.")
    cols = ", ".join(f'"{k}"' for k in values)
    placeholders = ", ".join(f":{k}" for k in values)
    with engine.begin() as conn:
        result = conn.execute(text(f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})'), values)
        pk = result.lastrowid
    return {"success": True, "id": pk}


def update_row(db: Session, table: str, pk_value: Any, changes: Dict[str, Any]) -> Dict[str, Any]:
    entry = _assert_known_table(db, table)
    pk_col = _pk_column(table)
    col_names = {c["name"] for c in entry["columns"]}
    changes = {k: v for k, v in (changes or {}).items() if k in col_names and k != pk_col}
    if not changes:
        raise TableError("No valid columns to update.")
    set_clause = ", ".join(f'"{k}" = :{k}' for k in changes)
    params = dict(changes)
    params["__pk"] = pk_value
    with engine.begin() as conn:
        result = conn.execute(text(f'UPDATE "{table}" SET {set_clause} WHERE "{pk_col}" = :__pk'), params)
    return {"success": True, "updated": result.rowcount}


def delete_row(db: Session, table: str, pk_value: Any) -> Dict[str, Any]:
    _assert_known_table(db, table)
    pk_col = _pk_column(table)
    with engine.begin() as conn:
        result = conn.execute(text(f'DELETE FROM "{table}" WHERE "{pk_col}" = :pk'), {"pk": pk_value})
    return {"success": True, "deleted": result.rowcount}


def create_table(db: Session, name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rejects a name colliding with ANY real Aegis table (not just the
    safe-to-browse ones — so a user can never shadow `users`,
    `aegis_account`, or anything else) or an existing user table. Always
    adds its own auto-increment `id` primary key — a user's own columns
    are never allowed to be the primary key, keeping update/delete's
    by-primary-key contract simple and reliable for every table this
    browser touches."""
    _validate_identifier(name, "table name")
    if name in Base.metadata.tables:
        raise TableError(f"'{name}' collides with an existing Aegis table name — pick a different name.")
    if db.query(UserCustomTable).filter(UserCustomTable.name == name).first():
        raise TableError(f"A table named '{name}' already exists.")
    if not columns:
        raise TableError("Add at least one column.")

    col_defs = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
    normalized = []
    seen = {"id"}
    for col in columns:
        col_name = (col or {}).get("name", "")
        _validate_identifier(col_name, "column name")
        if col_name in seen:
            raise TableError(f"Duplicate column name '{col_name}'.")
        seen.add(col_name)
        col_type = (col or {}).get("type", "text")
        if col_type not in _ALLOWED_COLUMN_TYPES:
            raise TableError(f"Unsupported column type '{col_type}' — use text, integer, real, or boolean.")
        nullable = "" if col.get("nullable", True) else " NOT NULL"
        col_defs.append(f'"{col_name}" {_ALLOWED_COLUMN_TYPES[col_type]}{nullable}')
        normalized.append({"name": col_name, "type": col_type, "nullable": bool(col.get("nullable", True))})

    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE "{name}" ({", ".join(col_defs)})'))

    db.add(UserCustomTable(name=name, columns_json=json.dumps(normalized)))
    db.commit()
    return {"success": True}


def drop_table(db: Session, name: str) -> Dict[str, Any]:
    """Only ever permitted for a name present in UserCustomTable — a real
    Aegis table can never be dropped through this feature, full stop."""
    row = db.query(UserCustomTable).filter(UserCustomTable.name == name).first()
    if not row:
        raise TableError(f"'{name}' isn't a user-created table — only tables created through this browser can be dropped.")
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE "{name}"'))
    db.delete(row)
    db.commit()
    return {"success": True}
