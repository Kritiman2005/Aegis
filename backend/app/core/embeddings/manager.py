"""
Aegis — Embedding Model Manager

Resolves a workflow vector node's chosen embedding model (data.embeddingModel
— a Marketplace-installed model's id, or unset for the app's own bundled
default, app.core.rag.processor.get_dense_model) to a ready embedder
instance. Loading model weights isn't cheap, so each distinct model is
instantiated once per process and cached — same reasoning as
app.core.agents.chat's LLM manager caching a loaded GGUF model rather than
reloading it per call.

Two backends, per the installed row's `backend` column
(app.api.marketplace_embeddings): "fastembed" for fastembed's own supported
models (a fastembed.TextEmbedding instance directly), "sentence_transformers"
for a custom Hugging Face repo the user typed in themselves — fastembed only
runs models from its own fixed, ONNX-converted list, so anything outside
that goes through sentence-transformers instead, wrapped in
_SentenceTransformerEmbedder so callers see the identical `.embed(texts)`
surface either way.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_INSTANCES: Dict[str, Any] = {}


class _SentenceTransformerEmbedder:
    """Adapts sentence_transformers.SentenceTransformer.encode() to
    fastembed.TextEmbedding.embed()'s shape — an iterable of per-text
    vectors, each with a numpy `.tolist()` — so every caller (e.g. the
    workflow engine's embedding node) can treat both backends identically."""

    def __init__(self, model):
        self._model = model

    def embed(self, texts: List[str]):
        return list(self._model.encode(list(texts), convert_to_numpy=True))


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
        backend = row.backend or "fastembed"

    if backend == "sentence_transformers":
        from sentence_transformers import SentenceTransformer
        instance = _SentenceTransformerEmbedder(SentenceTransformer(model_id, cache_folder=cache_dir))
    else:
        from fastembed import TextEmbedding
        instance = TextEmbedding(model_name=model_id, cache_dir=cache_dir)

    _INSTANCES[model_id] = instance
    return instance
