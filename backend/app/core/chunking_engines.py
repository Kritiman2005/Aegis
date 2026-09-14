"""
Aegis — chunking strategy registry.

app.core.rag.processor.chunk_text is a single, deliberately-tuned
recursive strategy (paragraph -> sentence -> word, heading-aware) used
unchanged everywhere in the app's own document-upload/RAG pipeline — this
module never touches that default path. It exists purely to give a
Workflow's Chunk node a choice of alternative strategies
(app.core.workflows.engine._run_chunk_node), the same "several choices,
one marked default" shape app.core.extraction_engines already uses for
per-format extractors.

Every strategy shares processor.py's own building blocks (_pack_units,
_split_sentences, _hard_split_words, _enforce_embed_token_limit) rather
than reimplementing packing/overlap/token-limit logic a second time, and
every strategy respects a document's own section-heading boundaries
(processor._HEADING_MARKER, set by the extractors — PDF via font size,
DOCX via paragraph style, MD via '#') by never merging text across one:
a chunk never silently straddles two different sections, whichever
strategy produced it.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _paragraphs_by_heading(text: str):
    """Splits text into (heading, paragraphs) groups — one group per
    section, in document order, `heading` is None for any text before the
    first heading (or when the document has none at all). Shared by every
    strategy below except "fixed" (which deliberately ignores structure
    entirely)."""
    from app.core.rag.processor import _HEADING_MARKER

    groups: List[tuple] = []
    current_heading: Optional[str] = None
    current: List[str] = []
    for line in text.split("\n"):
        para = line.strip()
        if not para:
            continue
        if para.startswith(_HEADING_MARKER):
            if current:
                groups.append((current_heading, current))
            current_heading = para[len(_HEADING_MARKER):].strip()
            current = []
            continue
        current.append(para)
    if current:
        groups.append((current_heading, current))
    return groups


def _recursive(text: str, chunk_size: int, overlap: int) -> List[str]:
    from app.core.rag.processor import chunk_text
    return chunk_text(text, chunk_size=chunk_size, overlap=overlap)


def _fixed(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Naive fixed-size word-count windows across the WHOLE text, ignoring
    paragraph/sentence/heading boundaries entirely — the fastest, most
    predictable strategy (every chunk but the last is exactly chunk_size
    words), at the cost of freely cutting mid-sentence. Good for already-
    clean, unstructured, or very short text where boundary-awareness adds
    nothing."""
    from app.core.rag.processor import _hard_split_words, _enforce_embed_token_limit
    return _enforce_embed_token_limit(_hard_split_words(text, chunk_size, overlap))


def _sentence(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Every sentence is its own atomic unit, packed up to chunk_size
    words per chunk (never split mid-sentence) — unlike "recursive", a
    chunk here is never a whole unsplit paragraph even when it would fit,
    so sentence-level boundaries are always respected. Good for dense
    prose (articles, transcripts) where sentence-level meaning matters
    more than paragraph structure."""
    from app.core.rag.processor import _split_sentences, _pack_units, _prefix_heading, _enforce_embed_token_limit

    chunks: List[str] = []
    for heading, paragraphs in _paragraphs_by_heading(text):
        sentences = [s for p in paragraphs for s in _split_sentences(p)]
        if not sentences:
            continue
        packed = _pack_units(sentences, chunk_size, overlap, joiner=" ")
        chunks.extend(_prefix_heading(packed, heading))
    return _enforce_embed_token_limit(chunks)


def _paragraph(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Every paragraph is its own atomic unit, packed up to chunk_size
    words per chunk — unlike "recursive", an oversized single paragraph is
    never broken down into sentences, it's hard-cut by word count instead
    (simpler, coarser). Good for already-structured documents (specs,
    contracts, docs with short paragraphs) where paragraph boundaries
    carry real meaning and a paragraph rarely needs further splitting."""
    from app.core.rag.processor import _pack_units, _hard_split_words, _prefix_heading, _enforce_embed_token_limit

    chunks: List[str] = []
    for heading, paragraphs in _paragraphs_by_heading(text):
        pending: List[str] = []
        for para in paragraphs:
            if len(para.split()) > chunk_size:
                if pending:
                    chunks.extend(_prefix_heading(_pack_units(pending, chunk_size, overlap, joiner="\n"), heading))
                    pending = []
                chunks.extend(_prefix_heading(_hard_split_words(para, chunk_size, overlap), heading))
                continue
            pending.append(para)
        if pending:
            chunks.extend(_prefix_heading(_pack_units(pending, chunk_size, overlap, joiner="\n"), heading))
    return _enforce_embed_token_limit(chunks)


STRATEGIES: List[Dict[str, Any]] = [
    {
        "id": "recursive", "name": "Recursive", "default": True, "fn": _recursive,
        "description": "Packs paragraphs together, falling back to sentences then words only for an oversized paragraph. The best general-purpose default — same strategy the app's own document uploads use.",
    },
    {
        "id": "paragraph", "name": "Paragraph", "default": False, "fn": _paragraph,
        "description": "Keeps each paragraph together as one unit, packed up to the chunk size. Good for structured documents (specs, contracts) where paragraph boundaries carry real meaning.",
    },
    {
        "id": "sentence", "name": "Sentence", "default": False, "fn": _sentence,
        "description": "Keeps each sentence together as one unit, packed up to the chunk size — never merges a whole paragraph into one chunk. Good for dense prose like articles or transcripts.",
    },
    {
        "id": "fixed", "name": "Fixed size", "default": False, "fn": _fixed,
        "description": "Naive fixed-size word windows across the whole text, ignoring paragraph/sentence boundaries entirely. Fastest and most predictable chunk sizes, at the cost of cutting mid-sentence.",
    },
]


def list_strategies() -> List[Dict[str, Any]]:
    """Strategy metadata only (no callables) — what the Marketplace/canvas UI needs."""
    return [{k: v for k, v in s.items() if k != "fn"} for s in STRATEGIES]


def chunk(text: str, chunk_size: int = 300, overlap: int = 50, strategy_id: Optional[str] = None) -> List[str]:
    """Runs the given strategy (or the default "recursive" one when
    strategy_id is unset or unrecognized) against text."""
    entry = next((s for s in STRATEGIES if s["id"] == strategy_id), None) if strategy_id else None
    if entry is None:
        entry = next(s for s in STRATEGIES if s["default"])
    return entry["fn"](text, chunk_size, overlap)
