import os
import uuid
import logging
import threading
from typing import List, Dict, Any
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

COLLECTION_NAME = "aegis_documents"

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
                        size=384,
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
                _dense_model = TextEmbedding("BAAI/bge-small-en-v1.5")
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
                _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _reranker


# ─── Text Extraction ──────────────────────────────────────────────────────────

def extract_text(file_path: str, file_type: str) -> str:
    ext = file_type.lower()
    try:
        if ext == 'pdf':
            doc = fitz.open(file_path)
            try:
                # needs_pass (not is_encrypted) is the one that actually
                # blocks get_text() below — a PDF can be "encrypted" with
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
                text_content = ""
                for page in doc:
                    text_content += page.get_text() + "\n"
                return text_content
            finally:
                # fitz.Document holds the file open for lazy per-page
                # access; never closing it leaks a file descriptor per
                # upload for the life of this long-running backend process.
                doc.close()

        elif ext in ['ppt', 'pptx']:
            prs = Presentation(file_path)
            text_content = ""
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text_content += shape.text + "\n"
            return text_content

        elif ext in ['txt', 'md', 'csv']:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()

        elif ext == 'docx':
            from docx import Document
            doc = Document(file_path)
            parts = [p.text for p in doc.paragraphs if p.text]
            # Tables aren't walked by doc.paragraphs at all — a docx with a
            # table and no surrounding prose would otherwise extract as
            # empty text.
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text for cell in row.cells if cell.text]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)

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

def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> List[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += (chunk_size - overlap)
    return chunks


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

    chunks = chunk_text(raw_text)
    logger.info(f"Generated {len(chunks)} chunks for document {document_id}.")
    
    # Generate Dense and Sparse Vectors
    from qdrant_client import models  # lazy import
    dense_vecs = list(get_dense_model().embed(chunks))
    sparse_vecs = list(get_sparse_model().embed(chunks))
    
    points = []
    for i, chunk in enumerate(chunks):
        # fastembed sparse returns SparseEmbedding object
        sv = sparse_vecs[i]
        
        point = models.PointStruct(
            id=str(uuid.uuid4()),
            vector={
                "text-dense": dense_vecs[i].tolist(),
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
        
    client = get_qdrant_client()
    with _qdrant_lock:
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
    logger.info(f"Successfully ingested document {document_id} into Qdrant.")


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


# ─── Advanced Hybrid Retrieval & Reranking ────────────────────────────────────

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

    # Query Qdrant with Reciprocal Rank Fusion (RRF) implicitly by querying both
    # Qdrant's query_points automatically fuses multiple prefetches
    with _qdrant_lock:
        results = get_qdrant_client().query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                models.Prefetch(
                    query=query_dense.tolist(),
                    using="text-dense",
                    limit=15,
                    filter=doc_filter
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=query_sparse.indices.tolist(),
                        values=query_sparse.values.tolist()
                    ),
                    using="text-sparse",
                    limit=15,
                    filter=doc_filter
                )
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=15
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
    
    filtered_chunks = []
    for i, chunk in enumerate(unique_chunks):
        score = float(scores[i])
        chunk["rerank_score"] = score
        filtered_chunks.append(chunk)
            
    filtered_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)
    return filtered_chunks[:top_k]
