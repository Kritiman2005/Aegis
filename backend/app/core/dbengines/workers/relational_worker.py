"""
Aegis — Relational Engine Worker (runs inside `uv run --with <package>`)

Invoked by app.core.dbengines.worker_runner, never imported directly by the
main backend process — this file only ever runs as a subprocess inside an
isolated uv environment, so it must import nothing beyond the standard
library plus whichever single engine package that environment installs.

Reads one JSON payload from stdin: {"engine", "path", "op", ...}. Writes one
JSON result line to stdout: {"ok": true, ...} or {"ok": false, "error": str}.

op:
  "migrate" — payload.statements: list[str] of DDL, executed in order.
  "query"   — payload.sql + payload.params (dict) -> {"rows": [...]}
  "execute" — payload.sql + payload.params (dict) -> {"rowcount": int}
"""

import json
import sys


def _duckdb(payload: dict) -> dict:
    import duckdb

    con = duckdb.connect(payload["path"])
    try:
        op = payload["op"]
        if op == "migrate":
            for stmt in payload["statements"]:
                con.execute(stmt)
            return {"ok": True}
        elif op == "query":
            cur = con.execute(payload["sql"], payload.get("params") or {})
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            return {"ok": True, "rows": rows}
        elif op == "execute":
            # DuckDB's Python cursor has no reliable rowcount — callers that
            # need affected-row counts should use "query" with a RETURNING
            # clause instead.
            con.execute(payload["sql"], payload.get("params") or {})
            return {"ok": True, "rowcount": -1}
        else:
            return {"ok": False, "error": f"Unknown op '{op}'."}
    finally:
        con.close()


_ENGINES = {"duckdb": _duckdb}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    engine = payload.get("engine")
    handler = _ENGINES.get(engine)
    if not handler:
        print(json.dumps({"ok": False, "error": f"Unsupported relational engine '{engine}'."}))
        return
    try:
        result = handler(payload)
    except Exception as e:
        result = {"ok": False, "error": str(e)}
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
