"""
Aegis — Vector Engine Dispatch

Same shape as relational.py's dispatch (create_store/upsert/search instead
of migrate/query/execute), for app.api.marketplace_databases (install-time
store creation) and the workflow "vector" node
(app.core.workflows.engine). qdrant runs in-process — it's already a
bundled dependency for the app's own document RAG (app.core.rag.processor)
— using its embedded (on-disk, no server) mode at a per-install path
distinct from the app's own aegis_documents_v2 collection. lancedb/chromadb
run through worker_runner.run_worker.
"""

from typing import Any, Dict, List, Optional

import anyio

from . import worker_runner
from .registry import get_engine

_DISTANCE_MAP = {"cosine": "Cosine", "euclidean": "Euclid", "dot": "Dot"}
_TABLE_NAME = "vectors"


class VectorError(Exception):
    pass


def _qdrant_create_store(path: str, dim: int, distance: str) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    dist = getattr(Distance, _DISTANCE_MAP.get(distance, "Cosine").upper())
    client = QdrantClient(path=path)
    try:
        if not client.collection_exists(_TABLE_NAME):
            client.create_collection(collection_name=_TABLE_NAME, vectors_config=VectorParams(size=dim, distance=dist))
    finally:
        client.close()


def _qdrant_upsert(path: str, ids: List[Any], vectors: List[List[float]], payloads: List[Dict]) -> int:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(path=path)
    try:
        points = [PointStruct(id=i, vector=v, payload=p or {}) for i, v, p in zip(ids, vectors, payloads)]
        client.upsert(collection_name=_TABLE_NAME, points=points)
        return len(points)
    finally:
        client.close()


def _qdrant_search(path: str, query_vector: List[float], top_k: int) -> List[Dict]:
    from qdrant_client import QdrantClient

    client = QdrantClient(path=path)
    try:
        response = client.query_points(collection_name=_TABLE_NAME, query=query_vector, limit=top_k)
        return [{"id": p.id, "score": p.score, "payload": p.payload} for p in response.points]
    finally:
        client.close()


async def create_store(engine_id: str, storage_path: str, dim: int, distance: str = "cosine") -> None:
    """The vector counterpart of relational.migrate — creates the empty
    collection/dataset a marketplace vector-DB install needs before any
    workflow node can upsert or search against it."""
    spec = get_engine(engine_id)
    if not spec or spec.category != "vector":
        raise VectorError(f"'{engine_id}' isn't a vector engine.")

    if engine_id == "qdrant":
        await anyio.to_thread.run_sync(_qdrant_create_store, storage_path, dim, distance)
        return

    if not spec.pip_package:
        raise VectorError(f"Engine '{engine_id}' has no worker package configured.")
    await worker_runner.run_worker(
        spec.pip_package, "vector_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "create_store", "dim": dim, "distance": distance},
    )


async def upsert(engine_id: str, storage_path: str, ids: List[Any], vectors: List[List[float]], payloads: List[Optional[Dict]]) -> int:
    spec = get_engine(engine_id)
    if not spec or spec.category != "vector":
        raise VectorError(f"'{engine_id}' isn't a vector engine.")

    if engine_id == "qdrant":
        return await anyio.to_thread.run_sync(_qdrant_upsert, storage_path, ids, vectors, payloads)

    if not spec.pip_package:
        raise VectorError(f"Engine '{engine_id}' has no worker package configured.")
    result = await worker_runner.run_worker(
        spec.pip_package, "vector_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "upsert", "ids": ids, "vectors": vectors, "payloads": payloads},
    )
    return result.get("count", len(ids))


async def search(engine_id: str, storage_path: str, query_vector: List[float], top_k: int = 10) -> List[Dict]:
    spec = get_engine(engine_id)
    if not spec or spec.category != "vector":
        raise VectorError(f"'{engine_id}' isn't a vector engine.")

    if engine_id == "qdrant":
        return await anyio.to_thread.run_sync(_qdrant_search, storage_path, query_vector, top_k)

    if not spec.pip_package:
        raise VectorError(f"Engine '{engine_id}' has no worker package configured.")
    result = await worker_runner.run_worker(
        spec.pip_package, "vector_worker.py",
        {"engine": engine_id, "path": storage_path, "op": "search", "query_vector": query_vector, "top_k": top_k},
    )
    return result.get("matches", [])
