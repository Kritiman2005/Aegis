"""
Aegis — bundled default model cache helpers

Aegis's own bundled default embedding model (BAAI/bge-base-en-v1.5) and
reranker (BAAI/bge-reranker-base) — see app.core.rag.processor's
get_dense_model/get_reranker — load directly by hardcoded Hugging Face repo
id, with no cache_dir of their own, so they land in the standard Hugging
Face hub cache (huggingface_hub's own default, not this app's) rather than
this app's EmbeddingModelRegistry/RerankerModelRegistry-tracked
directories. These helpers let Marketplace show and delete that real,
on-disk cache directly via huggingface_hub's own scan_cache_dir() — not a
guessed path — so those two bundled defaults are just as visible and
uninstallable as anything actually downloaded through this app's own
Marketplace flow. Deleting is always safe: both get_dense_model and
get_reranker transparently re-download on next use if their cache is gone,
exactly like a fresh install's first use.
"""

import logging
import shutil
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class CachedRepoInfo(TypedDict):
    size_on_disk: int
    path: str


def cached_repo_info(hf_repo_id: str) -> Optional[CachedRepoInfo]:
    """Real on-disk info for hf_repo_id if it's currently cached, else
    None. Never raises — a scan failure is treated as "not cached" rather
    than surfacing an unrelated error to the Marketplace UI."""
    try:
        from huggingface_hub import scan_cache_dir
        cache = scan_cache_dir()
        for repo in cache.repos:
            if repo.repo_id == hf_repo_id:
                return {"size_on_disk": repo.size_on_disk, "path": str(repo.repo_path)}
    except Exception as e:
        logger.debug(f"Could not scan Hugging Face cache for '{hf_repo_id}': {e}")
    return None


def delete_cached_repo(hf_repo_id: str) -> bool:
    """Removes hf_repo_id's real Hugging Face hub cache directory, if
    present. Returns whether anything was actually deleted."""
    info = cached_repo_info(hf_repo_id)
    if not info:
        return False
    shutil.rmtree(info["path"], ignore_errors=True)
    return True
