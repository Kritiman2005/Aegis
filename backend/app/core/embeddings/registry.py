"""
Aegis — Embedding Model Catalog

Wraps fastembed.TextEmbedding.list_supported_models() so the Marketplace's
Embedding Models category always reflects exactly what this app can
actually download and run — no separately hand-maintained list to drift
out of sync with whatever fastembed version is bundled.
"""

from functools import lru_cache
from typing import Any, Dict, List, Optional


@lru_cache(maxsize=1)
def list_catalog() -> List[Dict[str, Any]]:
    from fastembed import TextEmbedding

    return [
        {
            "id": m["model"],
            "display_name": m["model"],
            "dim": m["dim"],
            "size_gb": round(m["size_in_GB"], 2),
            "description": m["description"],
        }
        for m in TextEmbedding.list_supported_models()
    ]


def get_catalog_entry(model_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in list_catalog() if m["id"] == model_id), None)
