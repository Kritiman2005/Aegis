"""
Aegis — Default Pipeline seed

Inserts ONE starter workflow, connected as the global chat handler, so a
fresh install can actually chat immediately — built from the exact same
node kinds as anything a user would build themselves (see
app.core.workflows.engine's module docstring), not a separate settings
screen.

Deliberately minimal: chat_trigger -> llm "Reply" -> chat_reply "Send
Reply", nothing else — one LLM node, one reply, exactly what a fresh
install actually needs to hold a conversation. No auto document search,
no auto export/multi-part classification, no attachment-ingestion loop,
no export_document node either — all real, working branches in earlier
versions of this seed (see git history if you need any of these shapes
back), but every one of them made a fresh install's actual default
behavior more than "just chat", which isn't what this seed is for. A user
who wants document search, auto-classification, attachment ingestion, or
the chat composer's Export-menu wired up now builds that themselves on
the Workflows canvas, from the same node palette (embedding, vector,
reranker, extract, chunk, loop, logic, export_document) this seed used to
wire up automatically — none of those node kinds went away, only the
seed's opinion that everyone wants them wired by default did.

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
# Bumped 11 -> 13 -> 15 (12 and 14 both got applied, mid-edit, to a dev
# install with the OLD graph still in the file at that exact instant — a
# hot-reload race between uvicorn's file-watcher and a multi-edit sequence
# to this file, not a real intermediate shape either time; both times the
# fix was one more version bump once the file was stable again. 13 first
# replaced the old five-branch RAG+classify+ingestion tree with
# chat_trigger -> llm -> chat_reply -> export_document; 15 drops
# export_document too — two nodes, not four, see this module's docstring).
# Every existing install's seeded workflow gets overwritten with the new
# minimal shape on next startup (the normal self-healing overwrite this
# version-bump mechanism always does); a user who wants any of the old
# auto behavior (or the Export-menu node) back now builds it themselves on
# the Workflows canvas.
SEED_VERSION = 15

# Pre-merge identity — only used for the one-time migration described in
# this module's docstring, folding these two into the single row above.
_LEGACY_CHAT_KEY = "default_chat_pipeline"
_LEGACY_CHAT_NAME = "Aegis Default Chat Pipeline"
_LEGACY_INGESTION_KEY = "document_ingestion_pipeline"
_LEGACY_INGESTION_NAME = "Aegis Document Ingestion Pipeline"

BUILTIN_VECTOR_STORE_NAME = "Qdrant"
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


BUILTIN_AEGIS_DB_NAME = "SQLite"
BUILTIN_AEGIS_DB_ENGINE_ID = "aegis_app_db"


def ensure_builtin_aegis_database(db: Session) -> None:
    """
    Registers Aegis's own SQLite database (chat history, workflows,
    installed databases, ... — the same curated allowlist
    app.core.aegis_db_browser already exposes) as a normal-looking
    InstalledDatabase row, category "relational" — one real "database" node
    now covers BOTH a Marketplace-installed database of the user's own
    choosing (SQL query) AND this one (list/insert/update/delete through
    the safety-checked browser), rather than a second, separate node kind
    hardcoding "Aegis Database" as its own thing (see engine.py's
    _run_database_node for the engine_id==BUILTIN_AEGIS_DB_ENGINE_ID
    special-casing). is_builtin=True keeps it from ever being deleted
    through the ordinary Marketplace databases API. storage_path is unused
    for this engine_id (aegis_db_browser always operates through the app's
    own SessionLocal, never a raw file path) — kept non-empty only because
    the column itself is NOT NULL.
    """
    from app.db.models import InstalledDatabase

    row = db.query(InstalledDatabase).filter(InstalledDatabase.engine_id == BUILTIN_AEGIS_DB_ENGINE_ID).first()
    if row:
        if row.name != BUILTIN_AEGIS_DB_NAME:
            row.name = BUILTIN_AEGIS_DB_NAME
            db.commit()
        return
    db.add(InstalledDatabase(
        name=BUILTIN_AEGIS_DB_NAME,
        engine_id=BUILTIN_AEGIS_DB_ENGINE_ID,
        category="relational",
        storage_path="(app database)",
        status="ready",
        is_builtin=True,
    ))
    db.commit()
    logger.info(f"Registered built-in database '{BUILTIN_AEGIS_DB_NAME}'.")


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

    def _node(node_id: str, x: int, y: int, data: dict) -> dict:
        return {"id": node_id, "type": "workflowNode", "position": {"x": x, "y": y}, "data": data}

    nodes = [
        _node("trigger", 540, 40, {"label": "On chat message", "kind": "chat_trigger"}),
        _node("reply_llm", 540, 160, {
            # reply_llm's ONLY downstream edge (into "reply" below) is what
            # marks it as the real generator — see run_chat_workflow's
            # generation_ids detection.
            "label": "Reply", "kind": "llm", "isAi": False,
            "modelName": "", "instruction": build_chat_prompt(),
        }),
        _node("reply", 540, 280, {"label": "Send Reply", "kind": "chat_reply"}),
    ]
    edges = [
        {"id": "trigger-reply_llm", "source": "trigger", "target": "reply_llm", "data": {}},
        {"id": "reply_llm-reply", "source": "reply_llm", "target": "reply", "data": {}},
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
        # A genuinely fresh install — connect it as the global chat handler
        # immediately. This row's entire purpose is to BE Aegis's default
        # Chat Mode behavior (see this module's docstring); leaving it
        # unconnected meant every fresh install silently used the legacy
        # built-in ChatAgent pipeline instead, and none of a user's edits
        # to this "Default Pipeline" (its model picks, memory settings,
        # search-decision prompt, etc.) ever actually affected real chat
        # traffic until they noticed and hit "Connect to chat" themselves.
        # Never touches an EXISTING row's connection state — see the
        # `if existing:` branch above and this module's docstring on why
        # that's preserved across a seed content update.
        db.add(Workflow(
            name=SEED_WORKFLOW_NAME, graph_json=graph_json, seed_version=SEED_VERSION, seed_key=SEED_WORKFLOW_KEY,
            is_chat_handler=True,
        ))
        db.commit()
        logger.info(f"Seeded '{SEED_WORKFLOW_NAME}' workflow (version {SEED_VERSION}) and connected it as the chat handler.")
