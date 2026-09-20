"""
model_catalog.py — A small, hand-verified set of GGUF models spanning RAM
tiers, used to recommend a model that actually fits the user's machine
(à la AnythingLLM's setup flow) instead of leaving them to guess from a raw
HuggingFace search.

Each entry's (repo_id, filename) was verified (via the HF tree API — real
file, real size) to resolve to a single GGUF file — several equivalent
quants for larger models are split into multiple shard files (e.g.
"-00001-of-00003.gguf"), which the current single-file download endpoint
can't assemble, so only genuinely single-file quants are listed here.
Qwen's own official GGUF repos shard EVERY quant once the model reaches
14B, with no single-file option at all — bartowski's repos (a widely-used,
reputable community quantizer, the same one LM Studio/Ollama-adjacent
tooling pulls from) publish real single-file quants at every size instead,
so the 16GB tier and up all resolve through bartowski.

Tiers are sized for this app's actual shipping target — Mac, unified
memory, Metal GPU offload on by default (HardwareConfig.n_gpu_layers=-1
offloads every layer) — so the RAM math doesn't need to reserve separate
VRAM headroom the way a discrete-GPU PC would. Each min_ram_gb is derived
the same way ModelHub.tsx's fitsComfortably() sizes a quant against a
machine: needed_gb = file_gb * 1.3 (runtime + KV cache overhead), plus a
flat 2.5GB reserved for macOS + Electron, kept comfortably under the tier's
threshold rather than right at the edge.
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
        display_name="Qwen2.5 7B Instruct (Q4_K_M)",
        repo_id="bartowski/Qwen2.5-7B-Instruct-GGUF",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        approx_download_gb=4.4,
        min_ram_gb=16,
        description="Noticeably stronger reasoning — the sweet spot for a 16GB Mac.",
    ),
    CatalogEntry(
        key="pro",
        display_name="Qwen2.5 14B Instruct (Q4_K_M)",
        repo_id="bartowski/Qwen2.5-14B-Instruct-GGUF",
        filename="Qwen2.5-14B-Instruct-Q4_K_M.gguf",
        approx_download_gb=8.4,
        min_ram_gb=24,
        description="A real reasoning upgrade over 7B — built for 24GB+ Macs with memory to spare.",
    ),
    CatalogEntry(
        key="max",
        display_name="Qwen2.5 32B Instruct (Q3_K_M)",
        repo_id="bartowski/Qwen2.5-32B-Instruct-GGUF",
        filename="Qwen2.5-32B-Instruct-Q3_K_M.gguf",
        approx_download_gb=14.8,
        min_ram_gb=32,
        description="The biggest jump in capability this app offers — for 32GB+ Macs.",
    ),
    CatalogEntry(
        key="ultra",
        display_name="Qwen2.5 32B Instruct (Q6_K)",
        repo_id="bartowski/Qwen2.5-32B-Instruct-GGUF",
        filename="Qwen2.5-32B-Instruct-Q6_K.gguf",
        approx_download_gb=25.0,
        min_ram_gb=64,
        description="Near-full-precision 32B — maximum quality, for 64GB+ Macs.",
    ),
]


def recommend_model(total_ram_gb: float) -> Optional[CatalogEntry]:
    """
    Pick the most capable catalog entry that comfortably fits the machine's
    total RAM (each tier's min_ram_gb already includes headroom for macOS +
    Electron + KV cache — see llm_manager._estimate_ram_required_gb for the
    same math applied to an actual loaded model).
    """
    best = None
    for entry in MODEL_CATALOG:
        if total_ram_gb >= entry.min_ram_gb:
            best = entry  # tiers are ordered smallest -> largest; keep the last fit
    return best or MODEL_CATALOG[0]  # below every tier's threshold: still suggest the smallest
