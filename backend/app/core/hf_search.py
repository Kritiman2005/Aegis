"""
Aegis — Hugging Face Hub Search

Small shared REST helper for searching the Hugging Face Hub by free text,
used by the Marketplace's Embedding Models and Rerankers categories to let
a user pull in a model beyond each category's own curated/fixed list (see
app.api.marketplace_embeddings and app.api.marketplace_rerankers). Mirrors
app.api.models_hub.search_models's own approach — hits the REST API
directly with httpx rather than the huggingface_hub SDK, which stays
immune to SDK version differences (kwargs like `direction`/`tags` have
broken across huggingface_hub releases before).
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def search_models(query: str, filter_tag: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Raises RuntimeError on network/HTTP failure — callers surface that as
    a real error rather than silently reporting zero results."""
    if not query or not query.strip():
        return []

    params: Dict[str, Any] = {
        "search": query.strip(),
        "limit": limit,
        "sort": "downloads",
        "direction": -1,
        "full": "False",
    }
    if filter_tag:
        params["filter"] = filter_tag

    try:
        # No explicit verify= — main.py's truststore.inject_into_ssl() already
        # makes every SSL connection use the OS's own native trust evaluation.
        res = httpx.get(
            "https://huggingface.co/api/models",
            params=params, timeout=15.0,
        )
        res.raise_for_status()
        models_data = res.json()
    except httpx.HTTPError as e:
        logger.error(f"HF Hub search failed for query '{query}': {e}")
        raise RuntimeError(f"Could not reach Hugging Face: {e}") from e

    return [
        {
            "id": m.get("id"),
            "author": m.get("author"),
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "tags": m.get("tags", []),
        }
        for m in models_data
    ]
