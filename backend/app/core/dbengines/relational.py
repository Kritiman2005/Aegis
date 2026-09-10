"""
Aegis — Relational Engine Dispatch

One small interface (migrate/query/execute) that app.api.marketplace_databases
(install-time migration) and the workflow "database" node
(app.core.workflows.engine) both call through, regardless of which engine an
InstalledDatabase row actually points at. sqlite runs in-process (stdlib);
every other relational engine (duckdb today) runs through
worker_runner.run_worker, matching this package's __init__ docstring.
"""

import sqlite3
from typing import Any, Dict, List

import anyio

from . import worker_runner
from .registry import get_engine


class RelationalError(Exception):
    pass


def _sqlite_migrate(path: str, statements: List[str]) -> None:
    conn = sqlite3.connect(path)
    try:
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _sqlite_query(path: str, sql: str, params: Dict[str, Any]) -> List[Dict]:
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        if cur.description is None:
            # A write statement (INSERT/UPDATE/DELETE) run through the
            # single-query-field workflow node — commit rather than silently
            # rolling it back when the connection closes.
            conn.commit()
            return []
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _sqlite_execute(path: str, sql: str, params: Dict[str, Any]) -> int:
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


async def migrate(engine_id: str, storage_path: str, statements: List[str]) -> None:
    """Runs a list of DDL statements against a newly (or already) installed
    database — this is the "backend runs migrations and all backend stuff
    directly" step for a relational marketplace item's user-supplied schema."""
    spec = get_engine(engine_id)
    if not spec or spec.category != "relational":
        raise RelationalError(f"'{engine_id}' isn't a relational engine.")

    if engine_id == "sqlite":
        await anyio.to_thread.run_sync(_sqlite_migrate, storage_path, statements)
        return

    if not spec.pip_package:
        raise RelationalError(f"Engine '{engine_id}' has no worker package configured.")
    await worker_runner.run_worker(
        spec.pip_package, "relational_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "migrate", "statements": statements},
    )


async def run_query(engine_id: str, storage_path: str, sql: str, params: Dict[str, Any]) -> List[Dict]:
    spec = get_engine(engine_id)
    if not spec or spec.category != "relational":
        raise RelationalError(f"'{engine_id}' isn't a relational engine.")

    if engine_id == "sqlite":
        return await anyio.to_thread.run_sync(_sqlite_query, storage_path, sql, params)

    if not spec.pip_package:
        raise RelationalError(f"Engine '{engine_id}' has no worker package configured.")
    result = await worker_runner.run_worker(
        spec.pip_package, "relational_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "query", "sql": sql, "params": params},
    )
    return result.get("rows", [])


async def run_execute(engine_id: str, storage_path: str, sql: str, params: Dict[str, Any]) -> int:
    spec = get_engine(engine_id)
    if not spec or spec.category != "relational":
        raise RelationalError(f"'{engine_id}' isn't a relational engine.")

    if engine_id == "sqlite":
        return await anyio.to_thread.run_sync(_sqlite_execute, storage_path, sql, params)

    if not spec.pip_package:
        raise RelationalError(f"Engine '{engine_id}' has no worker package configured.")
    result = await worker_runner.run_worker(
        spec.pip_package, "relational_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "execute", "sql": sql, "params": params},
    )
    return result.get("rowcount", -1)
