import os
import re
import uuid
import logging
import threading
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

# ── Light extractors — safe to import at startup ──────────────────────────────
import fitz  # PyMuPDF
from pptx import Presentation

# ── ALL heavy ML libraries are lazy-imported inside functions ─────────────────
# qdrant_client, fastembed, and sentence_transformers all pull in PyTorch
# (~2 GB of DLLs) when imported. Importing them at module level causes
# the PyInstaller binary to crash immediately on Windows before /api/health
# can respond. They are imported inside the getter functions below, so the
# server starts in <1 second and the libraries load on first actual use.

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
data_dir = os.environ.get("AEGIS_DATA_DIR")
if data_dir:
    QDRANT_DB_DIR = Path(data_dir) / "qdrant_db"
else:
    QDRANT_DB_DIR = BASE_DIR / "qdrant_db"

# Versioned rather than a fixed name: the embedding model upgrade
# (BAAI/bge-small-en-v1.5, 384-dim -> BAAI/bge-base-en-v1.5, 768-dim) means
# vectors embedded under the old model are a different, incompatible
# dimensionality — Qdrant collections have a fixed vector size, so an
# in-place switch would either crash on the first upsert against the old
# collection or (if silently recreated) destroy the existing index with no
# recovery path. A new collection name sidesteps that entirely: the old
# collection is simply left alone (never opened, never written to again)
# while _migrate_documents_to_current_embedding below re-ingests every
# existing document into this one so search keeps working after the
# upgrade instead of silently going blind on anything uploaded before it.
COLLECTION_NAME = "aegis_documents_v2"

# ─── Lazy singletons ──────────────────────────────────────────────────────────

_qdrant_client = None
_dense_model    = None
_sparse_model   = None
_reranker       = None

# Qdrant's embedded/local mode persists to a plain sqlite3 connection created
# once, in init_qdrant(), on whichever thread calls it first (FastAPI startup —
# the main event loop thread). Every other call site touches that same
# connection from a background worker thread (document ingestion runs via
# anyio.to_thread.run_sync, chat's RAG search runs via db_executor), which
# sqlite3 rejects by default ("SQLite objects created in a thread can only be
# used in that same thread"). force_disable_check_same_thread=True below lifts
# that check, but does not make the connection safe for genuinely concurrent
# use — this lock serializes all access to it so ingestion and retrieval never
# touch it at the same instant.
_qdrant_lock = threading.Lock()

# Guards first-time initialization of the four lazy singletons below. Without
# this, main.py's startup preload thread (_preload_embedding_models) and a
# real ingestion/search request landing moments later can both see e.g.
# _dense_model is None at the same time and both start downloading/loading
# the same multi-hundred-MB model concurrently — doubling the network/disk
# work (and risking two processes writing the same on-disk model cache at
# once) instead of the second caller just waiting for the first's result.
# This is the actual cause of "ingestion takes forever" on a fresh install:
# not that a document is slow to process, but that two full model loads
# were racing each other for it. Double-checked locking (check outside the
# lock, re-check inside it) keeps the normal warm-model path lock-free.
_model_init_lock = threading.Lock()


def init_qdrant():
    """Called on FastAPI startup to bind QdrantClient to the main event loop thread."""
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient, models  # lazy import
        logger.info("Initializing QdrantClient on Uvicorn event loop thread...")
        _qdrant_client = QdrantClient(path=str(QDRANT_DB_DIR), force_disable_check_same_thread=True)
        if not _qdrant_client.collection_exists(COLLECTION_NAME):
            _qdrant_client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    "text-dense": models.VectorParams(
                        size=768,  # BAAI/bge-base-en-v1.5's real output dimension
                        distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    "text-sparse": models.SparseVectorParams(
                        modifier=models.Modifier.IDF
                    )
                }
            )


def get_qdrant_client():
    if _qdrant_client is None:
        init_qdrant()
    return _qdrant_client


def get_dense_model():
    global _dense_model
    if _dense_model is None:
        with _model_init_lock:
            if _dense_model is None:  # re-check: another thread may have just finished this
                from fastembed import TextEmbedding  # lazy import — pulls torch
                logger.info("Initializing Dense Embedding Model...")
                # Upgraded from bge-small-en-v1.5 (384-dim, ~33M params) —
                # meaningfully better semantic discrimination on longer/
                # denser documents, at a still-local-friendly ~109M params.
                # Output dimension changed (384 -> 768): see COLLECTION_NAME
                # and init_qdrant's vectors_config above for why that's a
                # new collection, not an in-place change.
                _dense_model = TextEmbedding("BAAI/bge-base-en-v1.5")
    return _dense_model


def get_sparse_model():
    global _sparse_model
    if _sparse_model is None:
        with _model_init_lock:
            if _sparse_model is None:
                from fastembed import SparseTextEmbedding  # lazy import — pulls torch
                logger.info("Initializing Sparse Embedding Model...")
                _sparse_model = SparseTextEmbedding("Qdrant/bm25")
    return _sparse_model


def get_reranker():
    global _reranker
    if _reranker is None:
        with _model_init_lock:
            if _reranker is None:
                from sentence_transformers import CrossEncoder  # lazy import — pulls torch
                logger.info("Initializing CrossEncoder Reranker...")
                # Upgraded from cross-encoder/ms-marco-MiniLM-L-6-v2 (6-layer,
                # ~22M params) — meaningfully better at discriminating
                # subtly-different candidates (e.g. two similarly-worded
                # sections of a long document) at a still-reasonable ~278M
                # params for local CPU inference. Its scores are on a
                # completely different scale (sigmoid-normalized [0, 1]
                # rather than raw logits) — see hybrid_search's
                # _MIN_RERANK_SCORE, re-calibrated for this model specifically.
                _reranker = CrossEncoder('BAAI/bge-reranker-base')
    return _reranker


# ─── Text Extraction ──────────────────────────────────────────────────────────

# Prefix marking a paragraph/line detected as a section heading during
# extraction (PDF: font-size based, DOCX: paragraph style based — see
# below). chunk_text() looks for this to track which heading each chunk
# falls under and prepend that context, then strips the marker itself —
# never reaches an embedding or the model. A control character rather than
# a printable string so it can never collide with real document content.
_HEADING_MARKER = "\x01HEADING\x01"
_MD_HEADING_RE = re.compile(r'^#{1,6}\s+\S')


def _extract_pdf_page_text(page) -> str:
    """
    Ports AnythingLLM's own PDF text-extraction algorithm (their
    collector/processSingleFile/convert/asPDF/PDFLoader — pdf.js-based)
    onto PyMuPDF's structured span data, rather than PyMuPDF's plain
    page.get_text(): walk every text span in extraction order, and insert a
    newline only when a span's Y-origin differs from the previous one — two
    spans at the same Y are the same visual line and get concatenated
    with no separator, same as theirs. Still PyMuPDF/MuPDF underneath (kept
    for its mature font/encoding handling — pdf.js has no particular edge
    here) — same line-detection algorithm, not the same library.

    Also detects section headings by font size: a line whose every span is
    meaningfully larger (>=15%) than the page's dominant (body) text size,
    and short enough to plausibly be a heading rather than a big pull-quote
    paragraph, gets wrapped in _HEADING_MARKER. Body size is picked by
    total character count rather than raw span count, so a page with one
    big heading and a lot of body text doesn't get skewed by the heading
    itself. A page with no real size variation (e.g. a scanned/OCR'd PDF
    where everything reports one font size) just never marks anything —
    degrades to the exact previous behavior, doesn't guess wrong.
    """
    text_dict = page.get_text("dict")

    # Group spans into lines by Y-origin first (same grouping the line-
    # detection above uses), carrying each span's font size along —
    # needed to decide heading-ness before we know how to render the line.
    lines: List[List[Tuple[str, float]]] = []
    last_y = None
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block; skip images etc.
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text:
                    continue
                y = span["origin"][1]
                if last_y is None or y != last_y:
                    lines.append([])
                lines[-1].append((span_text, span.get("size", 0)))
                last_y = y

    if not lines:
        return ""

    size_char_counts: Dict[float, int] = {}
    for line in lines:
        for text, size in line:
            key = round(size, 1)
            size_char_counts[key] = size_char_counts.get(key, 0) + len(text)
    body_size = max(size_char_counts, key=size_char_counts.get) if size_char_counts else 0

    parts: List[str] = []
    for i, line in enumerate(lines):
        line_text = "".join(t for t, _ in line)
        is_heading = (
            body_size > 0
            and line_text.strip()
            and len(line_text) < 100
            and all(size >= body_size * 1.15 for _, size in line)
        )
        if i > 0:
            parts.append("\n")
        parts.append(f"{_HEADING_MARKER}{line_text.strip()}" if is_heading else line_text)
    return "".join(parts)


def _extract_pptx_shape_text(shape) -> List[str]:
    """
    Ports AnythingLLM's PPTX extraction (their asOfficeMime.js, backed by the
    officeparser npm package) which grabs every text run on a slide
    regardless of what container it sits in — text boxes, tables, grouped
    shapes. The old code here did `if hasattr(shape, "text")`, which is
    False for a GraphicFrame holding a table — table content on a slide was
    silently dropped. This walks group shapes and tables explicitly instead
    of relying on that attribute.
    """
    parts: List[str] = []
    if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP — recurse into members
        for sub_shape in shape.shapes:
            parts.extend(_extract_pptx_shape_text(sub_shape))
    elif shape.has_table:
        for row in shape.table.rows:
            cells = [cell.text for cell in row.cells if cell.text]
            if cells:
                parts.append(" | ".join(cells))
    elif shape.has_text_frame and shape.text_frame.text:
        parts.append(shape.text_frame.text)
    return parts


def _extract_docx_body_text(parent) -> List[str]:
    """
    Ports AnythingLLM's DOCX extraction (their asDocx.js, backed by
    LangChain's DocxLoader / mammoth.js), which walks the document in
    original document order. The old code here read all paragraphs first,
    then appended all tables at the end — a document with a table between
    two paragraphs came out with its table content relocated to the very
    end, scrambling reading order relative to the source. This walks
    `parent`'s direct XML children in order, recursing into tables (and any
    table nested inside a cell) so paragraphs and table rows stay
    interleaved exactly as authored.

    `parent` must be a real python-docx wrapper (a Document or a _Cell),
    not a raw XML element — `.text` alone would work off raw XML fine, but
    heading detection below reads `paragraph.style.name`, which resolves
    through `self.part` up the real parent chain to the document's style
    definitions; a raw XML element has no `.part` and would just raise.
    Headings get wrapped in _HEADING_MARKER, same convention as the PDF
    font-size based detection — python-docx's own style NAME is a more
    reliable signal than any text heuristic could be here, Word already
    tags "Heading 1"/"Heading 2"/"Title" etc. explicitly.
    """
    from docx.document import Document as _Document
    from docx.oxml.ns import qn
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    if isinstance(parent, _Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError(f"_extract_docx_body_text expects a Document or _Cell, got {type(parent)}")

    parts: List[str] = []
    for child in parent_elm.iterchildren():
        if child.tag == qn('w:p'):
            paragraph = Paragraph(child, parent)
            text = paragraph.text
            if not text:
                continue
            is_heading = False
            try:
                style_name = (paragraph.style.name or "") if paragraph.style else ""
                is_heading = (
                    len(text) < 100
                    and ("heading" in style_name.lower() or style_name.lower() == "title")
                )
            except Exception:
                pass  # style resolution failed for any reason — just treat as normal text
            parts.append(f"{_HEADING_MARKER}{text.strip()}" if is_heading else text)
        elif child.tag == qn('w:tbl'):
            table = Table(child, parent)
            for row in table.rows:
                row_cells = []
                for cell in row.cells:
                    cell_text = " ".join(_extract_docx_body_text(cell))
                    if cell_text:
                        row_cells.append(cell_text)
                if row_cells:
                    parts.append(" | ".join(row_cells))
    return parts


def extract_text(file_path: str, file_type: str) -> str:
    ext = file_type.lower()
    try:
        if ext == 'pdf':
            doc = fitz.open(file_path)
            try:
                # needs_pass (not is_encrypted) is the one that actually
                # blocks extraction below — a PDF can be "encrypted" with
                # only owner-password restrictions and still open/extract
                # fine, in which case needs_pass is 0 despite is_encrypted
                # being true. Checking this explicitly turns what would
                # otherwise be silent empty/garbage text (or a cryptic
                # PyMuPDF error deep in the page loop) into one clear,
                # actionable failure.
                if doc.needs_pass:
                    raise ValueError(
                        "This PDF is password-protected — remove the password and re-upload."
                    )
                return "\n\n".join(_extract_pdf_page_text(page) for page in doc)
            finally:
                # fitz.Document holds the file open for lazy per-page
                # access; never closing it leaks a file descriptor per
                # upload for the life of this long-running backend process.
                doc.close()

        elif ext in ['ppt', 'pptx']:
            prs = Presentation(file_path)
            parts: List[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    parts.extend(_extract_pptx_shape_text(shape))
            return "\n".join(parts)

        elif ext == 'md':
            # Markdown already has an explicit, unambiguous heading
            # signal — no font-size guessing needed like PDF, no style
            # lookup needed like DOCX.
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.read().split("\n")
            marked = [
                f"{_HEADING_MARKER}{line.lstrip('#').strip()}" if _MD_HEADING_RE.match(line) else line
                for line in lines
            ]
            return "\n".join(marked)

        elif ext in ['txt', 'csv']:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()

        elif ext == 'docx':
            from docx import Document
            doc = Document(file_path)
            return "\n".join(_extract_docx_body_text(doc))

        elif ext == 'xlsx':
            from openpyxl import load_workbook
            # data_only=True reads each cell's last-calculated value rather
            # than its formula string — "=SUM(A1:A5)" is useless as
            # searchable text, the number it evaluated to isn't.
            wb = load_workbook(file_path, data_only=True, read_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(f"# {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)

        elif ext in ['png', 'jpg', 'jpeg']:
            # Images never reach extract_text at all — documents.py routes
            # them to vision (if a vision model is active) or rejects the
            # upload outright (if not) before ingestion ever starts. OCR is
            # no longer used as a fallback for image content.
            raise ValueError("Image ingestion is not supported — images are handled via vision, not RAG.")

        else:
            logger.warning(f"Unsupported file type for extraction: {ext}")
            return ""
    except Exception as e:
        logger.error(f"Error extracting text from {file_path}: {e}")
        raise e


# ─── Chunking ─────────────────────────────────────────────────────────────────

# Splits on whitespace that immediately follows a sentence-terminating
# punctuation mark — a deliberately simple heuristic (no NLP sentence
# tokenizer dependency), same tradeoff LangChain's own splitters make.
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(paragraph: str) -> List[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    return sentences or [paragraph]


def _hard_split_words(unit: str, chunk_size: int, overlap: int) -> List[str]:
    """Last-resort fallback: a single sentence (or a paragraph with no
    sentence boundaries at all, e.g. a heading) that alone still exceeds
    chunk_size. The only place left that cuts at an arbitrary word count
    rather than a real semantic boundary."""
    words = unit.split()
    pieces = []
    i = 0
    while i < len(words):
        pieces.append(" ".join(words[i:i + chunk_size]))
        i += (chunk_size - overlap)
    return pieces


def _pack_units(units: List[str], chunk_size: int, overlap: int, joiner: str) -> List[str]:
    """Greedily packs whole text units (paragraphs, or sentences within
    one oversized paragraph) into chunk_size-word groups. Overlap is
    approximated by carrying the previous group's last unit into the next
    one, rather than a fixed word count, so the boundary context handed
    forward is always a whole unit, never a mid-unit fragment."""
    packed: List[str] = []
    current: List[str] = []
    current_words = 0

    for unit in units:
        unit_words = len(unit.split())
        if current and current_words + unit_words > chunk_size:
            packed.append(joiner.join(current))
            current = [current[-1]] if overlap > 0 else []
            current_words = len(current[-1].split()) if current else 0
        current.append(unit)
        current_words += unit_words

    if current:
        packed.append(joiner.join(current))
    return packed


def _prefix_heading(chunks: List[str], heading: Optional[str]) -> List[str]:
    """Prepends the currently-active section heading (if any) to every
    chunk in a just-packed batch — see chunk_text's heading tracking."""
    if not heading:
        return chunks
    return [f"[Section: {heading}]\n{c}" for c in chunks]


def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> List[str]:
    """
    Multi-level recursive chunking, coarsest boundary first — same
    philosophy as LangChain's RecursiveCharacterTextSplitter (paragraph ->
    sentence -> word), so a chunk only ever splits at a finer boundary
    than it needs to:
      1. Paragraphs (newline-separated — how every extractor above already
         separates them, including docx table rows joined with " | ") are
         packed together up to chunk_size words.
      2. A paragraph that alone exceeds chunk_size is split into sentences
         first, which are then packed the same way — a long paragraph
         still breaks at sentence boundaries, not mid-sentence.
      3. Only a single sentence that alone exceeds chunk_size (rare — a
         heading, a run-on line with no terminal punctuation) falls back
         to a hard word-count cut.
    Document order is preserved throughout — pending normal paragraphs are
    flushed (packed and appended to the result) before an oversized
    paragraph's own chunks are appended, so chunks always come out in the
    same order their source paragraphs appeared in.

    Section headings (marked by the extractors above with _HEADING_MARKER —
    PDF via font size, DOCX via paragraph style, MD via '#') are tracked as
    we walk paragraphs in order and prepended to every chunk that falls
    under them ("[Section: Termination]\\n<chunk text>"), so a chunk deep in
    a subsection's body carries its own context instead of just being bare
    text with no idea what part of the document it's from — this matters
    for both embedding quality (a chunk embeds better with its section
    context attached) and for the model being able to say where an answer
    actually came from. A heading always starts a fresh pending batch
    (flushing whatever was pending under the previous heading first), so
    one packed batch never straddles two different sections. Documents
    with no detected headings (most .txt/.csv, or a PDF/DOCX where nothing
    qualified) behave exactly as before — current_heading just stays None
    throughout and _prefix_heading is a no-op.

    Also carries the last normal paragraph forward as leading context when
    transitioning into an oversized paragraph's sentence-split chunks —
    without this, that transition was a hard cut with no overlap at all
    (the overlap mechanism elsewhere only ever carries context within one
    packing pass, not across the paragraph/sentence mode switch), exactly
    the kind of boundary a document mixing short and very long paragraphs
    hits often.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: List[str] = []
    pending_normal: List[str] = []
    current_heading: Optional[str] = None

    def flush_normal() -> Optional[str]:
        last_unit = pending_normal[-1] if pending_normal else None
        if pending_normal:
            packed = _pack_units(pending_normal, chunk_size, overlap, joiner="\n")
            chunks.extend(_prefix_heading(packed, current_heading))
            pending_normal.clear()
        return last_unit if overlap > 0 else None

    for para in paragraphs:
        if para.startswith(_HEADING_MARKER):
            flush_normal()
            current_heading = para[len(_HEADING_MARKER):].strip()
            continue

        if len(para.split()) <= chunk_size:
            pending_normal.append(para)
            continue

        carry = flush_normal()
        pending_sentences: List[str] = [carry] if carry else []
        for sentence in _split_sentences(para):
            if len(sentence.split()) <= chunk_size:
                pending_sentences.append(sentence)
                continue
            if pending_sentences:
                packed = _pack_units(pending_sentences, chunk_size, overlap, joiner=" ")
                chunks.extend(_prefix_heading(packed, current_heading))
                pending_sentences = []
            chunks.extend(_prefix_heading(_hard_split_words(sentence, chunk_size, overlap), current_heading))
        if pending_sentences:
            packed = _pack_units(pending_sentences, chunk_size, overlap, joiner=" ")
            chunks.extend(_prefix_heading(packed, current_heading))

    flush_normal()
    return _enforce_embed_token_limit(chunks)


# bge-base-en-v1.5's real hard limit (BERT-based architecture,
# max_position_embeddings=512, verified against its actual HF config, not
# assumed). fastembed/onnxruntime does not warn or error when a chunk
# exceeds this — it silently truncates, so the tail of an oversized chunk
# is simply never embedded at all. chunk_text's packing above sizes
# chunks by WORD count (chunk_size=300), which is only a rough proxy for
# real token count — punctuation, numbers, and denser vocabulary all
# push the ratio up — so a chunk that looks reasonably sized by word
# count can still silently lose content this way, worse the more
# technical/dense the source text is (exactly the "large and complex
# documents" case this exists for). _enforce_embed_token_limit below is
# the correctness backstop: verifies every chunk against the model's
# real tokenizer and re-splits by real token count (not another word
# guess) anything still over budget.
_EMBED_MODEL_MAX_TOKENS = 512
# Reserves room for chunk_text's own "[Section: <heading>]\n" prefix,
# added by _prefix_heading above BEFORE this backstop runs — a chunk
# sized right at the limit before that prefix would still overflow once
# it's added, so the backstop budgets against the *narrower* margin.
_EMBED_TOKEN_SAFETY_MARGIN = 40


def _count_embed_tokens(text: str) -> int:
    """Real token count via the active dense embedding model's own
    tokenizer — same "measure reality, don't guess from a proxy"
    philosophy as chat.py's _count_tokens for the LLM/chat side.
    Tokenizing one string at a time (not a batch) so the result is never
    padded up to match a longer sibling in the same call."""
    if not text:
        return 0
    try:
        encoding = get_dense_model().model.tokenize([text])[0]
        return len(encoding.ids)
    except Exception:
        return len(text) // 4  # rough fallback, same proxy used elsewhere when real counting fails


def _enforce_embed_token_limit(chunks: List[str]) -> List[str]:
    """
    Final pass after chunk_text's word-based packing: verifies each chunk
    actually fits the embedding model's real token budget and re-splits
    (by real token count, not another word guess) anything that doesn't.
    A no-op — zero extra tokenizer calls beyond the one per chunk to
    verify — for the overwhelming majority of chunks, which are already
    comfortably under budget; the actual re-split path only runs for the
    rare dense outlier the word-based packing under-estimated.
    """
    budget = _EMBED_MODEL_MAX_TOKENS - _EMBED_TOKEN_SAFETY_MARGIN
    result: List[str] = []
    for chunk in chunks:
        token_count = _count_embed_tokens(chunk)
        if token_count <= budget:
            result.append(chunk)
            continue

        logger.info(f"Chunk measured at {token_count} tokens (budget {budget}) — re-splitting by real token count.")
        words = chunk.split()
        # Proportional first guess from this chunk's own words-to-tokens
        # ratio, then verify (and shrink further if still over) rather
        # than trusting the estimate.
        target_words = max(10, int(len(words) * budget / max(token_count, 1)))
        for piece in _hard_split_words(chunk, target_words, overlap=0):
            if _count_embed_tokens(piece) <= budget:
                result.append(piece)
                continue
            sub_words = piece.split()
            while sub_words and _count_embed_tokens(" ".join(sub_words)) > budget:
                sub_words = sub_words[: max(1, len(sub_words) // 2)]
            if sub_words:
                result.append(" ".join(sub_words))
    return result


# ─── Ingestion ────────────────────────────────────────────────────────────────

# Nothing bounded how much text a single document could hand to chunk_text —
# a genuinely huge PDF/DOCX (a full textbook, a scanned archive) could
# generate thousands of chunks with no limit, each needing its own embedding
# pass. Slow on CPU-only hardware, and it's what could make an otherwise-
# legitimate large document collide with documents.py's own
# _INGEST_TIMEOUT_SECONDS and get killed as if ingestion were hung rather
# than just processing a lot of real content. ~2M chars (~350k words) caps
# chunking at roughly 1400 chunks — generous for a real document, bounded
# enough to keep embedding time sane.
_MAX_EXTRACTED_CHARS = 2_000_000


def ingest_document(document_id: int, file_path: str, file_type: str, filename: str):
    logger.info(f"Ingesting document {document_id}: {filename}")

    raw_text = extract_text(file_path, file_type)
    if len(raw_text) > _MAX_EXTRACTED_CHARS:
        logger.warning(
            f"Document {document_id} ({filename}) extracted to {len(raw_text):,} chars — "
            f"truncating to {_MAX_EXTRACTED_CHARS:,}."
        )
        raw_text = raw_text[:_MAX_EXTRACTED_CHARS]
    if not raw_text.strip():
        # Raise rather than return — the caller (process_upload_task) marks the
        # document "ready" on a normal return, which previously made empty-text
        # ingestion (a blank page, or an image with no OCR-readable text) look
        # like a success with zero searchable content actually indexed.
        ext = file_type.lower()
        if ext in ('png', 'jpg', 'jpeg'):
            raise ValueError(
                "No readable text found in this image via OCR. This app can only "
                "search images for printed/on-screen text — it can't describe or "
                "reason about visual content unless the active model supports vision."
            )
        raise ValueError("No extractable text was found in this file.")

    from app.core import context_config as ctx_cfg
    _chat_cfg = ctx_cfg.get("chat")
    chunks = chunk_text(
        raw_text,
        chunk_size=_chat_cfg.get("chunk_size", 300),
        overlap=_chat_cfg.get("chunk_overlap", 50),
    )
    logger.info(f"Generated {len(chunks)} chunks for document {document_id}.")
    hybrid_embed_and_upsert(document_id, chunks, filename)


def hybrid_embed_and_upsert(document_id: int, chunks: List[str], filename: str) -> int:
    """
    The real embed+store half of ingest_document, split out so
    app.core.workflows.engine's generic "vector" node can call the exact
    same hybrid dense+sparse indexing for its aegis_hybrid special case
    once a workflow's own Extract/Chunk nodes have already produced
    `chunks` — rather than the generic dense-only embedding those nodes'
    own "embedding" node output would give, which can't reproduce this
    collection's hybrid dense+sparse point shape (see _run_vector_node's
    upsert branch). ingest_document itself now just extracts+chunks a raw
    file and calls this. Returns the number of chunks actually indexed.

    Embed and upsert in batches rather than one call over the whole
    document (matching AnythingLLM's maxConcurrentChunks=25 pattern). For a
    large document (hundreds+ chunks), embedding everything in a single
    fastembed/onnxruntime call holds the full batch's tensors in memory at
    once and produces no visible progress or partial results until the
    entire thing finishes — on this app's RAM-constrained target hardware
    that's exactly the profile that stalls or gets killed. Batching keeps
    peak memory bounded to one batch and lets already-embedded chunks land
    in Qdrant progressively, so a late failure doesn't throw away earlier
    work.
    """
    from qdrant_client import models  # lazy import
    _EMBED_BATCH_SIZE = 25
    client = get_qdrant_client()
    total_points = 0
    for batch_start in range(0, len(chunks), _EMBED_BATCH_SIZE):
        batch_chunks = chunks[batch_start:batch_start + _EMBED_BATCH_SIZE]
        dense_vecs = list(get_dense_model().embed(batch_chunks))
        sparse_vecs = list(get_sparse_model().embed(batch_chunks))

        points = []
        for j, chunk in enumerate(batch_chunks):
            i = batch_start + j
            # fastembed sparse returns SparseEmbedding object
            sv = sparse_vecs[j]

            point = models.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    "text-dense": dense_vecs[j].tolist(),
                    "text-sparse": models.SparseVector(
                        indices=sv.indices.tolist(),
                        values=sv.values.tolist()
                    )
                },
                payload={
                    "document_id": document_id,
                    "chunk_index": i,
                    "filename": filename,
                    "content": chunk
                }
            )
            points.append(point)

        with _qdrant_lock:
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
        total_points += len(points)
        logger.info(
            f"Document {document_id}: embedded+upserted batch "
            f"{batch_start}-{batch_start + len(batch_chunks)} of {len(chunks)} chunks."
        )

    logger.info(f"Successfully ingested document {document_id} into Qdrant ({total_points} chunks).")
    return total_points


def delete_document_points(document_id: int) -> None:
    """Remove all indexed chunks for a document from Qdrant (used by the Files panel's delete action)."""
    from qdrant_client import models  # lazy import
    client = get_qdrant_client()
    doc_filter = models.Filter(
        must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
    )
    with _qdrant_lock:
        client.delete(collection_name=COLLECTION_NAME, points_selector=doc_filter)
    logger.info(f"Deleted Qdrant points for document {document_id}.")


# One-time marker (not a DB row — this is purely internal bookkeeping, not
# a user-facing setting) proving _migrate_documents_to_current_embedding
# below has already run, so a re-ingestion pass over every document
# doesn't repeat on every single server start. Lives next to the Qdrant
# DB itself, same directory scoping as everything else in this file.
_EMBEDDING_MIGRATION_MARKER = QDRANT_DB_DIR.parent / ".embedding_v2_migrated"


def migrate_documents_to_current_embedding() -> None:
    """
    Re-ingests every document already marked "ready" through the current
    extract -> chunk -> embed pipeline, so upgrading the embedding model
    (see get_dense_model/COLLECTION_NAME above) doesn't silently leave
    every document uploaded before the upgrade unsearchable — their old
    embeddings live in the abandoned old collection, which nothing reads
    from anymore. Safe to call more than once (ingestion is idempotent —
    upserts new points under fresh ids, doesn't corrupt anything), but
    gated behind a one-time marker file so it doesn't redo this full pass
    on every ordinary startup. Meant to run in a background thread (see
    main.py) — blocking, and re-embedding many/large documents can
    genuinely take a while.
    """
    if _EMBEDDING_MIGRATION_MARKER.exists():
        return

    from app.db.database import SessionLocal
    from app.db.models import UserDocument
    db = SessionLocal()
    try:
        ready_docs = db.query(UserDocument).filter(UserDocument.status == "ready").all()
        # Snapshot the few fields ingest_document needs before closing the
        # session — the loop below can take minutes for many documents,
        # far longer than this connection should be held open.
        doc_specs = [(d.id, d.file_path, d.file_type, d.filename) for d in ready_docs]
    finally:
        db.close()

    if not doc_specs:
        _EMBEDDING_MIGRATION_MARKER.touch()
        return

    logger.info(f"[Embedding migration] Re-ingesting {len(doc_specs)} document(s) into {COLLECTION_NAME}...")
    failed = 0
    for document_id, file_path, file_type, filename in doc_specs:
        try:
            ingest_document(document_id, file_path, file_type, filename)
        except Exception as e:
            failed += 1
            logger.warning(f"[Embedding migration] Failed to re-ingest document {document_id} ('{filename}'): {e}")

    logger.info(
        f"[Embedding migration] Done — {len(doc_specs) - failed}/{len(doc_specs)} document(s) re-ingested"
        + (f", {failed} failed (see warnings above)." if failed else ".")
    )
    _EMBEDDING_MIGRATION_MARKER.touch()


# ─── Advanced Hybrid Retrieval & Reranking ────────────────────────────────────

# Floor matches the old fixed limit exactly (so a small document's
# retrieval behavior is unchanged), scales up at 20% of the document's
# actual chunk count for anything bigger, capped so a huge document can't
# make a single search rerank an unbounded number of candidates. Extracted
# as its own function so the scaling math is unit-testable without a live
# Qdrant collection.
_MIN_PREFETCH_LIMIT = 15
_MAX_PREFETCH_LIMIT = 100


def _scale_prefetch_limit(total_chunks: int) -> int:
    """
    Fixed top-15 prefetch (before reranking) doesn't scale with document
    size — for a document with hundreds of chunks, the true best match has
    to survive into that same fixed top-15 by raw embedding/BM25 similarity
    alone, or the reranker (a much better relevance judge) never gets to
    see it at all. Widening the funnel proportionally to how many chunks
    actually exist directly targets that: a 20-chunk document still gets
    the same top-15 as before (near-exhaustive already), a 500-chunk
    document gets 100 (the cap) instead of the same top-15 a tiny document
    would get.
    """
    return min(_MAX_PREFETCH_LIMIT, max(_MIN_PREFETCH_LIMIT, total_chunks // 5))


def hybrid_search(query: str, conversation_id: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    1. Qdrant Native Hybrid Search (Fusion)
    2. Filter by Conversation ID
    3. Rerank via CrossEncoder to get Top K
    """
    logger.info(f"Running Qdrant hybrid search for query: {query}")

    # We must first fetch the user's document IDs for this conversation
    from app.db.database import SessionLocal
    from app.db.models import UserDocument
    db = SessionLocal()
    docs = db.query(UserDocument).filter(UserDocument.conversation_id == conversation_id).all()
    db.close()

    valid_doc_ids = [d.id for d in docs]
    if not valid_doc_ids:
        return []

    query_dense = list(get_dense_model().embed([query]))[0]
    query_sparse = list(get_sparse_model().embed([query]))[0]

    from qdrant_client import models  # lazy import — qdrant_client no longer imported at module level

    doc_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=valid_doc_ids)
            )
        ]
    )

    with _qdrant_lock:
        total_chunks = get_qdrant_client().count(
            collection_name=COLLECTION_NAME, count_filter=doc_filter, exact=True
        ).count
    prefetch_limit = _scale_prefetch_limit(total_chunks)

    # Query Qdrant with Reciprocal Rank Fusion (RRF) implicitly by querying both
    # Qdrant's query_points automatically fuses multiple prefetches
    with _qdrant_lock:
        results = get_qdrant_client().query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                models.Prefetch(
                    query=query_dense.tolist(),
                    using="text-dense",
                    limit=prefetch_limit,
                    filter=doc_filter
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=query_sparse.indices.tolist(),
                        values=query_sparse.values.tolist()
                    ),
                    using="text-sparse",
                    limit=prefetch_limit,
                    filter=doc_filter
                )
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=prefetch_limit
        )
    
    unique_chunks = []
    for point in results.points:
        unique_chunks.append({
            "id": point.id,
            "content": point.payload.get("content", ""),
            "document_id": point.payload.get("document_id"),
            "filename": point.payload.get("filename"),
            "fusion_score": point.score
        })
        
    if not unique_chunks:
        return []
        
    # Rerank
    logger.info(f"Reranking {len(unique_chunks)} fused chunks...")
    reranker = get_reranker()
    pairs = [[query, chunk["content"]] for chunk in unique_chunks]
    scores = reranker.predict(pairs)

    for i, chunk in enumerate(unique_chunks):
        chunk["rerank_score"] = float(scores[i])
    unique_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

    # Empirically calibrated against this exact model (BAAI/bge-reranker-
    # base), not assumed — and re-derived from scratch when the reranker
    # was upgraded from cross-encoder/ms-marco-MiniLM-L-6-v2, since that
    # model output raw unbounded logits (-1.29 for a genuine match, -7.9 to
    # -11.2 for noise) while this one outputs sigmoid-normalized scores in
    # [0, 1] — the old -6.0 cutoff would have been meaningless here (it
    # sits below the entire possible range, letting everything through).
    # Real test queries against real document content: clearly off-topic
    # queries and vague non-factual ones ("what is this", "summarize")
    # scored 0.00003-0.0006; a genuine but loosely-worded paraphrase
    # ("cancellation policy" for a termination clause) scored 0.038; a
    # strong direct match scored 0.99+. 0.01 sits with a >15x margin above
    # the true noise ceiling while still keeping loose paraphrases — the
    # same "clearly separated band" philosophy as the original threshold,
    # just re-measured for this model's actual score scale. Below this
    # cutoff, every remaining candidate is closer to "unrelated" than
    # "on-topic", so returning them as "Relevant excerpts" would just be
    # confidently wrong — an empty result here correctly falls through to
    # the "no relevant chunks" path in chat.py rather than injecting noise.
    _MIN_RERANK_SCORE = 0.01
    relevant = [c for c in unique_chunks if c["rerank_score"] >= _MIN_RERANK_SCORE]
    return relevant[:top_k]
