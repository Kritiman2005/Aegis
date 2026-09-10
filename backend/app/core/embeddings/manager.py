"""
Aegis — Embedding Model Manager

Resolves a workflow vector node's chosen embedding model (data.embeddingModel
— a Marketplace-installed model's id, or unset for the app's own bundled
default, app.core.rag.processor.get_dense_model) to a ready
fastembed.TextEmbedding instance. Loading model weights isn't cheap, so
each distinct model is instantiated once per process and cached — same
reasoning as app.core.agents.chat's LLM manager caching a loaded GGUF model
rather than reloading it per call.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_INSTANCES: Dict[str, Any] = {}


def get_embedder(model_id: Optional[str] = None):
    """Raises ValueError if model_id names a model that isn't installed or
    hasn't finished downloading — surfaced by the caller (the workflow
    engine) as that node's failure, not a silent fallback to the default
    model, since that would quietly change a workflow's results."""
    if not model_id:
        from app.core.rag.processor import get_dense_model
        return get_dense_model()

    if model_id in _INSTANCES:
        return _INSTANCES[model_id]

    from app.db.database import SessionLocal
    from app.db.models import EmbeddingModelRegistry

    with SessionLocal() as db:
        row = db.query(EmbeddingModelRegistry).filter(EmbeddingModelRegistry.model_id == model_id).first()
        if not row:
            raise ValueError(f"Embedding model '{model_id}' isn't installed — download it from the Marketplace first.")
        if row.status != "downloaded":
            raise ValueError(f"Embedding model '{model_id}' isn't ready yet (status: {row.status}).")
        cache_dir = row.cache_dir

    from fastembed import TextEmbedding
    instance = TextEmbedding(model_name=model_id, cache_dir=cache_dir)
    _INSTANCES[model_id] = instance
    return instance
