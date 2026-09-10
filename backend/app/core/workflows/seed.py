"""
Aegis — Default Pipeline seed

Inserts ONE starter workflow so a user has a real, fully-editable example of
Aegis's own default Chat Mode behavior sitting right in the Workflows
canvas — built from the exact same node kinds as anything they'd build
themselves (see app.core.workflows.engine's module docstring), not a
separate settings screen. Everything happens in a single tree starting at
"On chat message" and ending at the reply — document ingestion for a
chat-attached file is a real branch of THAT SAME tree (a loop over
this-turn's not-yet-indexed attachments, feeding a visible Extract -> Chunk
-> Embedding -> Vector chain), not a separate parallel pipeline, because
every document upload in this app is already scoped to one specific
conversation (app.api.documents's /upload requires conversation_id) — an
upload was never really a separate, chat-independent event to begin with.

Built entirely from the minimal, n8n-style node set (see
app.core.workflows.engine's module docstring): a "decide"/"classify" step
is just a plain "llm" node configured with a structured-output schema —
not a dedicated node kind — and whether to even bother searching or
classifying is a visible "logic" gate, not invisible Python control flow.

Shape of the one tree, read top to bottom:
  chat_trigger
    -> loop (itemsField "pending_attachments") -> extract -> chunk
       -> embedding -> vector (upsert, bundled aegis_hybrid store)
       -> [ordering-only edge into "decide" below — see
          _trace_loop_body_chain/_run_loop_node in engine.py for why a
          join node needs a SECOND inbound edge, from something besides
          the previous chain step, to stop being treated as part of the
          loop's own body: "decide" is also fed directly by chat_trigger]
    -> llm "Decide: Need Document Search?" (outputFields: needs_search,
       whole_document, query — DEFAULT_DECIDE_SEARCH_PROMPT's exact shape)
         -> logic "Needs search?" (needs_search is_true)
              -> embedding "Embed Query" -> vector "Search documents"
                 (search, same bundled store — its own real dense+sparse
                 re-embed wins there, but a Marketplace-installed store
                 genuinely uses Embed Query's vector as-is)
    -> logic "Looks export/multi-part?" (message matches_regex — an
       approximation of _classify_export_and_compound's real regex
       pre-gate, close enough to keep skipping the classify call on a
       plain message)
         -> llm "Classify Export & Multi-part" (outputFields: is_export,
            format, parts — DEFAULT_TURN_CLASSIFIER_PROMPT's exact shape)
    -> llm "Reply" (model config lives ONLY on "llm" nodes — this is the
       one wired directly into "chat_reply" below, with nothing else
       wired after it, so engine.py's run_chat_workflow treats it as the
       real generator: folds in skill guidance itself, see
       includeSkillGuidance; every other "llm" node above is a plain,
       non-streaming judgment call, unaffected)
         -> chat_reply "Send Reply" (a thin "send" marker, no config of
            its own — see _run_chat_generation_node/run_chat_workflow)
    -> export_document "Export Reply" (only turns the reply into a file
       if requested; an explicit chat_ctx export_format from the
       composer's Export menu always wins over the classifier's guess —
       see _run_export_document_node)

"Memory & Entities" is deliberately NOT wired into this seed — plenty of
turns confirm no entities at all, so it was often a visibly empty node in
a fresh install; the "memory_context" kind stays fully supported (and in
the node palette) for anyone who wants to add it back by hand.

Ingestion into the bundled aegis_hybrid store is real, not a placeholder:
_run_vector_node's upsert branch re-embeds the upstream chunk text with
the exact dense+sparse indexing app.core.rag.processor.hybrid_embed_and_
upsert uses (a generic Embedding node's dense-only output can't reproduce
that hybrid shape, so — like "search" mode already does — it's ignored in
favor of the real thing), and _run_loop_node marks the attachment's
UserDocument "ready" once its chain finishes, so a later turn in the same
conversation won't redo the work. A DIFFERENT, Marketplace-installed
store's generic dense-only upsert still works exactly as before if a user
swaps the Vector node's store.

Bump SEED_VERSION whenever the tree's shape changes. On startup, the
existing row for this seed (found by seed_key — see below) with an older
(or missing/legacy) seed_version gets its graph_json overwritten with the
current shape — otherwise an install that already has an old copy of this
demo would keep showing it forever. Its is_chat_handler connection state,
and any name the user gave it, are both preserved across the overwrite. A
user-authored workflow is never touched by this — seed_key/seed_version
are only ever set on this one row, never on anything the user creates
themselves.

This seed is identified by seed_key, a stable value never shown in the UI
— NOT by name. name is a plain editable text field on the canvas toolbar
(WorkflowsView.tsx); matching a seed purely by name breaks the moment a
user renames it (even temporarily, then back, e.g. while trying out a
different name): the next startup's by-name lookup misses the renamed row
and inserts a fresh duplicate under the canonical name, permanently
orphaning the original under its new name. seed_default_pipeline handles
three layers of one-time migration for installs that predate this: (1) a
row matched only by name (from before seed_key existed at all) is adopted
by _find_or_adopt_seed_row; (2) a still-separate pair of "Aegis Default
Chat Pipeline" / "Aegis Document Ingestion Pipeline" rows (from before the
two trees lived on one canvas) is folded into one, keeping whichever row
also had a live connection flag set; (3) that merged-but-still-two-trees
shape (is_ingestion_handler on the same row as is_chat_handler, from a
version in between) is superseded by this version's single tree the same
way any other seed_version bump replaces the whole graph_json — its
is_ingestion_handler flag is simply dropped, since there's no longer a
separate "On document upload" tree on this row to hold that connection
(the document_upload_trigger kind, and /set-ingestion-handler, still work
fine for a user's own custom workflow that wants that separation back).
"""

import json
import logging

from sqlalchemy.orm import Session

from app.db.models import Workflow

logger = logging.getLogger(__name__)

SEED_WORKFLOW_NAME = "Aegis Default Pipeline"
SEED_WORKFLOW_KEY = "default_pipeline"
SEED_VERSION = 9

# Pre-merge identity — only used for the one-time migration described in
# this module's docstring, folding these two into the single row above.
_LEGACY_CHAT_KEY = "default_chat_pipeline"
_LEGACY_CHAT_NAME = "Aegis Default Chat Pipeline"
_LEGACY_INGESTION_KEY = "document_ingestion_pipeline"
_LEGACY_INGESTION_NAME = "Aegis Document Ingestion Pipeline"

BUILTIN_VECTOR_STORE_NAME = "Qdrant — hybrid + BM25 + rerank (bundled)"
BUILTIN_VECTOR_ENGINE_ID = "aegis_hybrid"


def ensure_builtin_vector_store(db: Session) -> None:
    """
    Registers Aegis's own bundled hybrid document store (the real
    dense+BM25+reranked Qdrant collection app.core.rag.processor.
    hybrid_search already searches) as a normal-looking InstalledDatabase
    row, category "vector" — so it shows up as a real, selectable option
    in any workflow's Vector store node, right alongside a user's own
    Marketplace-installed ones (see engine.py's _run_vector_node for the
    engine_id=="aegis_hybrid" special-casing that makes selecting it
    actually run the real hybrid search rather than generic dense search).
    is_builtin=True keeps it from ever being deleted through the ordinary
    Marketplace databases API (app.api.marketplace_databases). Call this
    BEFORE seed_default_pipeline — that seed looks this row up by
    engine_id to point its Vector node at it by default.
    """
    from app.db.models import InstalledDatabase
    from app.core.rag.processor import QDRANT_DB_DIR

    row = db.query(InstalledDatabase).filter(InstalledDatabase.engine_id == BUILTIN_VECTOR_ENGINE_ID).first()
    if row:
        if row.name != BUILTIN_VECTOR_STORE_NAME:
            row.name = BUILTIN_VECTOR_STORE_NAME
            db.commit()
        return
    db.add(InstalledDatabase(
        name=BUILTIN_VECTOR_STORE_NAME,
        engine_id=BUILTIN_VECTOR_ENGINE_ID,
        category="vector",
        storage_path=str(QDRANT_DB_DIR),
        status="ready",
        is_builtin=True,
    ))
    db.commit()
    logger.info(f"Registered built-in vector store '{BUILTIN_VECTOR_STORE_NAME}'.")


def _find_or_adopt_seed_row(db: Session, seed_key: str, by_name: str) -> Workflow | None:
    """
    Looks up a seed row by its stable seed_key. If none is stamped yet (an
    install upgrading from before seed_key existed), falls back to every
    row currently sharing the given name, adopts the most authoritative
    one (connected as a live handler > highest seed_version > most
    recently updated) by stamping seed_key onto it, and deletes the rest —
    see this module's docstring for why matching by name alone eventually
    produces exactly these orphaned duplicates. Returns None when there's
    no row at all under that key or name yet.
    """
    existing = db.query(Workflow).filter(Workflow.seed_key == seed_key).first()
    if existing:
        return existing

    candidates = db.query(Workflow).filter(Workflow.name == by_name).all()
    if not candidates:
        return None

    def _rank(w: Workflow):
        return (
            1 if getattr(w, "is_chat_handler", False) or getattr(w, "is_ingestion_handler", False) else 0,
            w.seed_version or -1,
            w.updated_at or w.created_at,
        )

    candidates.sort(key=_rank, reverse=True)
    survivor, duplicates = candidates[0], candidates[1:]
    survivor.seed_key = seed_key
    for dup in duplicates:
        logger.warning(f"Deleting orphaned duplicate seed row '{dup.name}' (id={dup.id}) — adopted id={survivor.id} as the real '{seed_key}'.")
        db.delete(dup)
    db.commit()
    return survivor


def seed_default_pipeline(db: Session) -> None:
    existing = _find_or_adopt_seed_row(db, SEED_WORKFLOW_KEY, SEED_WORKFLOW_NAME)

    if not existing:
        # One-time migration from before the chat and ingestion trees lived
        # on a single canvas — fold whichever of the two old rows exist
        # into one, preserving both connection flags rather than losing
        # either one's live-handler state.
        chat_row = _find_or_adopt_seed_row(db, _LEGACY_CHAT_KEY, _LEGACY_CHAT_NAME)
        ingestion_row = _find_or_adopt_seed_row(db, _LEGACY_INGESTION_KEY, _LEGACY_INGESTION_NAME)
        if chat_row or ingestion_row:
            existing = chat_row or ingestion_row
            other = ingestion_row if existing is chat_row else chat_row
            if other and other.id != existing.id:
                existing.is_chat_handler = bool(existing.is_chat_handler or other.is_chat_handler)
                existing.is_ingestion_handler = bool(existing.is_ingestion_handler or other.is_ingestion_handler)
                logger.warning(
                    f"Merging '{other.name}' (id={other.id}) into '{existing.name}' (id={existing.id}) — "
                    f"the chat and document-upload pipelines now live in a single workflow."
                )
                db.delete(other)
            existing.seed_key = SEED_WORKFLOW_KEY
            existing.name = SEED_WORKFLOW_NAME
            db.commit()

    if existing and (existing.seed_version or 0) >= SEED_VERSION:
        return

    from app.prompts.chat import build_chat_prompt
    from app.core.agents.chat import DEFAULT_DECIDE_SEARCH_PROMPT, DEFAULT_TURN_CLASSIFIER_PROMPT
    from app.db.models import InstalledDatabase

    builtin_store = db.query(InstalledDatabase).filter(InstalledDatabase.engine_id == BUILTIN_VECTOR_ENGINE_ID).first()

    def _node(node_id: str, x: int, y: int, data: dict) -> dict:
        return {"id": node_id, "type": "workflowNode", "position": {"x": x, "y": y}, "data": data}

    builtin_id = builtin_store.id if builtin_store else None

    # Approximates _classify_export_and_compound's real regex pre-gate
    # (_EXPORT_HINT_RE OR (_COMPOUND_QUESTION_MARK_RE AND
    # _COMPOUND_CONNECTOR_RE)) as one pattern for one "logic" node —
    # close enough to keep skipping the classify LLM call on a plain
    # message, not an exact reproduction (see this module's docstring).
    _CLASSIFY_GATE_PATTERN = (
        r"\b(export|download|save|convert|pdf|docx?|xlsx|excel|word|spreadsheet|file)\b"
        r"|(?=.*\?)(?=.*\b(and|also|as well as|additionally)\b)"
    )

    nodes = [
        _node("trigger", 540, 40, {"label": "On chat message", "kind": "chat_trigger"}),

        # Ingestion branch — a loop over this turn's not-yet-indexed
        # attachments, feeding a real, visible Extract -> Chunk ->
        # Embedding -> Vector chain (see this module's docstring).
        _node("ingest_loop", 80, 160, {"label": "For each new attachment", "kind": "loop", "itemsField": "pending_attachments"}),
        _node("extract", 80, 280, {
            "label": "Extract text", "kind": "extract", "isAi": False,
            "filePath": "", "staticInputs": {},
        }),
        _node("chunk", 80, 400, {
            "label": "Chunk text", "kind": "chunk", "isAi": False,
            "chunkSize": 300, "overlap": 50,
        }),
        _node("embedding", 80, 520, {"label": "Embedding", "kind": "embedding", "isAi": False}),
        _node("vector_ingest", 80, 640, {
            "label": "Store in document index", "kind": "vector", "isAi": False,
            "operation": "upsert", "databaseId": builtin_id,
        }),

        # Retrieval branch — a plain "llm" node does the judgment call
        # (structured output, not a dedicated node kind), a "logic" node
        # makes the resulting gate visible, then Embed Query -> Search
        # mirrors the ingestion branch's own shape.
        _node("decide", 540, 160, {
            "label": "Decide: Need Document Search?", "kind": "llm", "isAi": False,
            "modelName": "", "instruction": DEFAULT_DECIDE_SEARCH_PROMPT,
            "outputFields": [
                {"name": "needs_search", "type": "boolean", "description": "true only if the document's content is needed to answer"},
                {"name": "whole_document", "type": "boolean", "description": "true if asking about the document broadly rather than one specific fact"},
                {"name": "query", "type": "string", "description": "a short focused search phrase, if needs_search and not whole_document"},
            ],
        }),
        _node("search_gate", 540, 280, {"label": "Needs search?", "kind": "logic", "field": "needs_search", "operator": "is_true"}),
        _node("embedding_query", 540, 400, {"label": "Embed Query", "kind": "embedding", "isAi": False}),
        _node("vector_search", 540, 520, {
            "label": "Search documents", "kind": "vector", "isAi": False,
            "operation": "search", "topK": 5,
            "databaseId": builtin_id,
        }),

        _node("classify_gate", 940, 160, {"label": "Looks export/multi-part?", "kind": "logic", "field": "message", "operator": "matches_regex", "value": _CLASSIFY_GATE_PATTERN}),
        _node("classifier", 940, 280, {
            "label": "Classify Export & Multi-part", "kind": "llm", "isAi": False,
            "modelName": "", "instruction": DEFAULT_TURN_CLASSIFIER_PROMPT,
            "outputFields": [
                {"name": "is_export", "type": "boolean", "description": "true only if the user wants a FILE created from this conversation"},
                {"name": "format", "type": "string", "description": "pdf, docx, or xlsx, if is_export"},
                {"name": "parts", "type": "array", "description": "distinct questions/requests, only if 2+ genuinely separate asks"},
            ],
        }),

        _node("reply_llm", 540, 660, {
            "label": "Reply", "kind": "llm", "isAi": False,
            "modelName": "", "instruction": build_chat_prompt(), "includeSkillGuidance": True,
        }),
        _node("reply", 540, 780, {"label": "Send Reply", "kind": "chat_reply"}),
        _node("export", 540, 900, {"label": "Export Reply", "kind": "export_document"}),
    ]
    edges = [
        {"id": "trigger-ingest_loop", "source": "trigger", "target": "ingest_loop", "data": {}},
        {"id": "ingest_loop-extract", "source": "ingest_loop", "target": "extract", "data": {"outputField": "file_path", "inputField": "filePath"}},
        {"id": "extract-chunk", "source": "extract", "target": "chunk", "data": {}},
        {"id": "chunk-embedding", "source": "chunk", "target": "embedding", "data": {}},
        {"id": "embedding-vector_ingest", "source": "embedding", "target": "vector_ingest", "data": {}},
        # Purely structural: forces "decide" to only run once this turn's
        # ingestion loop has fully finished (see this module's docstring
        # and _trace_loop_body_chain) — decide itself reads no upstream
        # data, so trigger-decide alone would otherwise make this edge's
        # source ambiguous between "loop body" and "join point".
        {"id": "vector_ingest-decide", "source": "vector_ingest", "target": "decide", "data": {}},
        {"id": "trigger-decide", "source": "trigger", "target": "decide", "data": {}},

        {"id": "decide-search_gate", "source": "decide", "target": "search_gate", "data": {}},
        {"id": "search_gate-embedding_query", "source": "search_gate", "target": "embedding_query", "data": {}},
        {"id": "embedding_query-vector_search", "source": "embedding_query", "target": "vector_search", "data": {}},

        {"id": "trigger-classify_gate", "source": "trigger", "target": "classify_gate", "data": {}},
        {"id": "classify_gate-classifier", "source": "classify_gate", "target": "classifier", "data": {}},

        {"id": "vector_search-reply_llm", "source": "vector_search", "target": "reply_llm", "data": {}},
        {"id": "classifier-reply_llm", "source": "classifier", "target": "reply_llm", "data": {}},
        # reply_llm's ONLY downstream edge — this is what marks it (not
        # any other "llm" node above) as the real generator; see
        # run_chat_workflow's generation_ids detection.
        {"id": "reply_llm-reply", "source": "reply_llm", "target": "reply", "data": {}},
        {"id": "reply-export", "source": "reply", "target": "export", "data": {}},
        {"id": "classifier-export", "source": "classifier", "target": "export", "data": {}},
    ]

    graph_json = json.dumps({"nodes": nodes, "edges": edges})
    if existing:
        old_version = existing.seed_version
        existing.graph_json = graph_json
        existing.seed_version = SEED_VERSION
        # This shape has no "On document upload" tree of its own anymore —
        # an is_ingestion_handler connection from a pre-merge version of
        # this row would otherwise point at a trigger node that no longer
        # exists in the graph it's now overwritten with.
        existing.is_ingestion_handler = False
        db.commit()
        logger.info(f"Updated '{SEED_WORKFLOW_NAME}' from seed_version {old_version} to {SEED_VERSION}.")
    else:
        db.add(Workflow(name=SEED_WORKFLOW_NAME, graph_json=graph_json, seed_version=SEED_VERSION, seed_key=SEED_WORKFLOW_KEY))
        db.commit()
        logger.info(f"Seeded '{SEED_WORKFLOW_NAME}' workflow (version {SEED_VERSION}).")
