"""
model_catalog.py — A small, hand-verified set of GGUF models spanning RAM
tiers, used to recommend a model that actually fits the user's machine
(à la AnythingLLM's setup flow) instead of leaving them to guess from a raw
HuggingFace search.

Each entry's (repo_id, filename) was verified to resolve to a real, single
GGUF file — several equivalent quants for larger models are split into
multiple shard files (e.g. "-00001-of-00002.gguf"), which the current
single-file download endpoint can't assemble, so only genuinely single-file
quants are listed here.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CatalogEntry:
    key: str
    display_name: str
    repo_id: str
    filename: str
    approx_download_gb: float
    min_ram_gb: int  # minimum *total* system RAM this tier targets
    description: str


MODEL_CATALOG: List[CatalogEntry] = [
    CatalogEntry(
        key="small",
        display_name="Qwen2.5 1.5B Instruct (Q4_K_M)",
        repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        approx_download_gb=1.1,
        min_ram_gb=4,
        description="Fast and lightweight — the safe choice on machines with limited RAM.",
    ),
    CatalogEntry(
        key="medium",
        display_name="Qwen2.5 3B Instruct (Q4_K_M)",
        repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF",
        filename="qwen2.5-3b-instruct-q4_k_m.gguf",
        approx_download_gb=2.1,
        min_ram_gb=8,
        description="Balanced quality and speed — a solid default for most laptops.",
    ),
    CatalogEntry(
        key="large",
        display_name="Qwen2.5 7B Instruct (Q3_K_M)",
        repo_id="Qwen/Qwen2.5-7B-Instruct-GGUF",
        filename="qwen2.5-7b-instruct-q3_k_m.gguf",
        approx_download_gb=3.8,
        min_ram_gb=16,
        description="Noticeably stronger reasoning — for machines with more RAM to spare.",
    ),
]


def recommend_model(total_ram_gb: float) -> Optional[CatalogEntry]:
    """
    Pick the most capable catalog entry that comfortably fits the machine's
    total RAM (each tier's min_ram_gb already includes headroom for OS +
    Electron + KV cache — see llm_manager._estimate_ram_required_gb for the
    same math applied to an actual loaded model).
    """
    best = None
    for entry in MODEL_CATALOG:
        if total_ram_gb >= entry.min_ram_gb:
            best = entry  # tiers are ordered smallest -> largest; keep the last fit
    return best or MODEL_CATALOG[0]  # below every tier's threshold: still suggest the smallest
