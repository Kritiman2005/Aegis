"""
Aegis — Embedding Model Manager

Resolves a workflow vector node's chosen embedding model (data.embeddingModel
— a Marketplace-installed model's id, or unset for the app's own bundled
default, app.core.rag.processor.get_dense_model) to a ready embedder
instance. Loading model weights isn't cheap, so each distinct model is
instantiated once per process and cached — same reasoning as
app.core.agents.chat's LLM manager caching a loaded GGUF model rather than
reloading it per call.

Three backends. Two are local, per the installed row's `backend` column
(app.api.marketplace_embeddings): "fastembed" for fastembed's own supported
models (a fastembed.TextEmbedding instance directly), "sentence_transformers"
for a custom Hugging Face repo the user typed in themselves — fastembed only
runs models from its own fixed, ONNX-converted list, so anything outside
that goes through sentence-transformers instead, wrapped in
_SentenceTransformerEmbedder so callers see the identical `.embed(texts)`
surface either way. The third isn't a registry row at all: a model_id
prefixed "mcp:" routes to _MCPEmbedder, a connected MCP server's own
embedding tool (see app.core.embeddings.registry.list_mcp_candidates) —
Aegis's integration point for an embedding model it doesn't bundle itself,
same "mcp:<server>:<tool>" convention app.core.extraction_engines and
app.core.chunking_engines use for their own MCP escape hatch.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_INSTANCES: Dict[str, Any] = {}


def _parse_vector(raw: Any) -> List[float]:
    """Best-effort unwrap of an MCP embedding tool's response into a flat
    list of floats: a bare JSON array of numbers is used directly; common
    wrapper shapes (a plain {"embedding": [...]}/{"vector": [...]}, or an
    OpenAI-style {"data": [{"embedding": [...]}]}) are unwrapped first."""
    import json
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("MCP embedding tool didn't return a JSON vector.")
    if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
        return [float(x) for x in parsed]
    if isinstance(parsed, dict):
        for key in ("embedding", "vector", "values"):
            value = parsed.get(key)
            if isinstance(value, list) and all(isinstance(x, (int, float)) for x in value):
                return [float(x) for x in value]
        data = parsed.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            value = data[0].get("embedding")
            if isinstance(value, list):
                return [float(x) for x in value]
    raise ValueError("MCP embedding tool's response wasn't a recognizable vector shape.")


class _MCPEmbedder:
    """Adapts a connected MCP server's embedding tool to the same
    `.embed(texts) -> per-text vector with .tolist()` surface as the
    fastembed/sentence_transformers backends below, so the workflow
    engine's embedding node (and anything downstream expecting a plain
    numpy-like vector) treats all three identically. Called once per text
    — an MCP tool has no standard batch-embedding convention to rely on,
    unlike fastembed/sentence_transformers' own real batch APIs."""

    def __init__(self, server_name: str, tool_name: str):
        self._server = server_name
        self._tool = tool_name

    def embed(self, texts: List[str]):
        import numpy as np
        from app.mcp.registry import mcp_registry
        from app.core.embeddings.registry import _guess_text_arg_name

        if mcp_registry.get_server_for_tool(self._tool) != self._server:
            raise ValueError(
                f"MCP tool '{self._tool}' from server '{self._server}' isn't connected right now — "
                f"reconnect it from Connectors and try again."
            )
        tool_def = next((t for t in mcp_registry.list_all_tools() if t.get("name") == self._tool), None)
        arg_name = _guess_text_arg_name(tool_def)
        if not arg_name:
            raise ValueError(f"MCP tool '{self._tool}' doesn't declare a text parameter Aegis recognizes.")

        vectors = []
        for text in texts:
            raw = mcp_registry.call_tool(self._tool, {arg_name: text})
            vectors.append(np.array(_parse_vector(raw), dtype=float))
        return vectors


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
    model, since that would quietly change a workflow's results.

    model_id prefixed "mcp:" (see app.core.embeddings.registry.
    list_mcp_candidates) returns an _MCPEmbedder instead of anything in
    the local registry below — Aegis's integration point for an embedding
    model it doesn't bundle itself."""
    if not model_id:
        from app.core.rag.processor import get_dense_model
        return get_dense_model()

    if model_id in _INSTANCES:
        return _INSTANCES[model_id]

    if model_id.startswith("mcp:"):
        _, server_name, tool_name = model_id.split(":", 2)
        instance = _MCPEmbedder(server_name, tool_name)
        _INSTANCES[model_id] = instance
        return instance

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
