"""
Aegis — Vector Engine Worker (runs inside `uv run --with <package>`)

Same contract as relational_worker.py (stdin/stdout JSON, stdlib + exactly
one third-party package), for the vector engines that aren't already
bundled with the app (qdrant runs in-process instead — see ../vector.py).

op:
  "create_store" — payload.dim, payload.distance -> creates the table/
    collection if it doesn't already exist.
  "upsert"  — payload.ids, payload.vectors, payload.payloads (parallel lists)
  "search"  — payload.query_vector, payload.top_k -> {"matches": [...]}
"""

import json
import sys

TABLE_NAME = "vectors"


def _lancedb(payload: dict) -> dict:
    import lancedb
    import pyarrow as pa

    db = lancedb.connect(payload["path"])
    op = payload["op"]

    if op == "create_store":
        if TABLE_NAME not in db.table_names():
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), payload["dim"])),
                pa.field("payload", pa.string()),
            ])
            db.create_table(TABLE_NAME, schema=schema)
        return {"ok": True}

    elif op == "upsert":
        tbl = db.open_table(TABLE_NAME)
        rows = [
            {"id": str(i), "vector": v, "payload": json.dumps(p, default=str)}
            for i, v, p in zip(payload["ids"], payload["vectors"], payload["payloads"])
        ]
        (tbl.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows))
        return {"ok": True, "count": len(rows)}

    elif op == "search":
        tbl = db.open_table(TABLE_NAME)
        results = tbl.search(payload["query_vector"]).limit(payload.get("top_k", 10)).to_list()
        matches = [
            {"id": r["id"], "score": r.get("_distance"), "payload": json.loads(r["payload"])}
            for r in results
        ]
        return {"ok": True, "matches": matches}

    return {"ok": False, "error": f"Unknown op '{op}'."}


def _chromadb(payload: dict) -> dict:
    import chromadb

    client = chromadb.PersistentClient(path=payload["path"])
    op = payload["op"]

    if op == "create_store":
        client.get_or_create_collection(TABLE_NAME, metadata={"hnsw:space": payload.get("distance") or "cosine"})
        return {"ok": True}

    elif op == "upsert":
        coll = client.get_or_create_collection(TABLE_NAME)
        coll.upsert(
            ids=[str(i) for i in payload["ids"]],
            embeddings=payload["vectors"],
            metadatas=[p or {} for p in payload["payloads"]],
        )
        return {"ok": True, "count": len(payload["ids"])}

    elif op == "search":
        coll = client.get_or_create_collection(TABLE_NAME)
        res = coll.query(query_embeddings=[payload["query_vector"]], n_results=payload.get("top_k", 10))
        ids = res.get("ids", [[]])[0]
        distances = res.get("distances", [[]])[0]
        metadatas = res.get("metadatas", [[]])[0]
        matches = [
            {"id": ids[i], "score": distances[i] if i < len(distances) else None, "payload": metadatas[i] if i < len(metadatas) else {}}
            for i in range(len(ids))
        ]
        return {"ok": True, "matches": matches}

    return {"ok": False, "error": f"Unknown op '{op}'."}


_ENGINES = {"lancedb": _lancedb, "chromadb": _chromadb}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    engine = payload.get("engine")
    handler = _ENGINES.get(engine)
    if not handler:
        print(json.dumps({"ok": False, "error": f"Unsupported vector engine '{engine}'."}))
        return
    try:
        result = handler(payload)
    except Exception as e:
        result = {"ok": False, "error": str(e)}
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
