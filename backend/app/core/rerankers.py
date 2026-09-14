"""
Aegis — Reranker model catalog + manager

Mirrors app.core.embeddings' shape (registry.py + manager.py combined into
one small module here, since — unlike fastembed's TextEmbedding.
list_supported_models() — sentence_transformers has no such catalog API to
wrap, so this is a short hand-maintained list instead) for a Workflow's
"reranker" node (app.core.workflows.engine._run_reranker_node) to pick a
cross-encoder from. Nothing downloads until the user picks one from the
Marketplace's Rerankers category (app.api.marketplace_rerankers) —
consistent with every other Marketplace category (see
app.core.marketplace's module docstring). Left unset on the node, it falls
back to the app's own bundled default (app.core.rag.processor.get_reranker,
unchanged — every direct, non-workflow caller of hybrid_search keeps using
that exact same bundled model too).
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CATALOG: List[Dict[str, Any]] = [
    {
        "id": "BAAI/bge-reranker-base",
        "display_name": "BAAI bge-reranker-base",
        "size_gb": 1.1,
        "description": "Aegis's own bundled default — a small, fast cross-encoder good for most document sets.",
    },
    {
        "id": "BAAI/bge-reranker-v2-m3",
        "display_name": "BAAI bge-reranker-v2-m3",
        "size_gb": 2.3,
        "description": "Larger, multilingual cross-encoder — better accuracy on harder queries, slower per search.",
    },
    {
        "id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "display_name": "MS MARCO MiniLM-L-6-v2",
        "size_gb": 0.1,
        "description": "Tiny, very fast cross-encoder — a lighter-weight option for large documents or slower machines.",
    },
]


def get_catalog_entry(model_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in CATALOG if m["id"] == model_id), None)


_INSTANCES: Dict[str, Any] = {}


def get_cross_encoder(model_id: Optional[str] = None):
    """Raises ValueError if model_id names a model that isn't installed or
    hasn't finished downloading — surfaced by the caller (the workflow
    engine) as that node's failure, not a silent fallback to the bundled
    default, since that would quietly change a workflow's results."""
    if not model_id:
        from app.core.rag.processor import get_reranker
        return get_reranker()

    if model_id in _INSTANCES:
        return _INSTANCES[model_id]

    from app.db.database import SessionLocal
    from app.db.models import RerankerModelRegistry

    with SessionLocal() as db:
        row = db.query(RerankerModelRegistry).filter(RerankerModelRegistry.model_id == model_id).first()
        if not row:
            raise ValueError(f"Reranker '{model_id}' isn't installed — download it from the Marketplace first.")
        if row.status != "downloaded":
            raise ValueError(f"Reranker '{model_id}' isn't ready yet (status: {row.status}).")
        cache_dir = row.cache_dir

    from sentence_transformers import CrossEncoder
    instance = CrossEncoder(model_id, cache_folder=cache_dir)
    _INSTANCES[model_id] = instance
    return instance
