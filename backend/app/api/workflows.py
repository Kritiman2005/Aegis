"""
Aegis — Workflows API (/api/workflows)

CRUD for user-designed workflow graphs plus a run trigger. The graph itself
(nodes + edges) is stored and returned verbatim in React Flow's own shape —
this API never interprets it beyond validating it's present JSON; all the
actual interpretation (topological order, tool dispatch, AI-node prompts)
lives in app.core.workflows.engine, run as a background task the same way
app.api.connectors.py's custom MCP connect already is.
"""

import json
import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Workflow, WorkflowRun, WorkflowVersion
from app.core.workflows.engine import run_workflow, _reachable_subgraph, _upstream_node_ids, _downstream_node_ids
from app.core.agents.chat import ChatAgent
from app.mcp.registry import mcp_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# Kept per workflow_id in workflow_versions — old ones are pruned on every
# insert so editing a workflow constantly (autosave-style) can't grow this
# table without bound.
WORKFLOW_VERSION_LIMIT = 30


class WorkflowPayload(BaseModel):
    name: str
    graph: Dict[str, Any]  # {"nodes": [...], "edges": [...]}
    model_config = {"defer_build": True}


@router.get("/tools")
def list_available_tools():
    """Every tool the node palette can offer: MCP-connected + local, same
    shape _format_tool_for_planner already expects (name/description/
    inputSchema) so the canvas can render argument fields directly from it.
    Each is tagged with "server" (the owning MCP server's name, or None for
    a local tool) so the palette's MCP category can drill server -> tool
    instead of showing one flat list."""
    mcp_tools = []
    for t in mcp_registry.list_all_tools():
        t = dict(t)
        t["server"] = mcp_registry.get_server_for_tool(t["name"])
        mcp_tools.append(t)

    local_tools = [dict(t, server=None) for t in ChatAgent.list_local_tools_for_palette()]
    return {"tools": mcp_tools + local_tools}


@router.get("/chunking-strategies")
def list_chunking_strategies():
    """Every chunking strategy a Chunk node can pick (app.core.chunking_engines) —
    built-in algorithms shipped with the app, not a Marketplace-installed
    capability, so this is its own small endpoint rather than going
    through the Marketplace tools listing the way per-format extraction
    engines do."""
    from app.core import chunking_engines
    return {"strategies": chunking_engines.list_strategies()}


@router.get("/mcp-extraction-candidates")
def list_mcp_extraction_candidates():
    """Every connected MCP tool that plausibly extracts text from a file —
    offered as an Extract node engine choice alongside the bundled
    per-format engines (app.core.extraction_engines.list_mcp_candidates).
    Not format-scoped upstream (Aegis has no way to know which file types a
    given MCP tool actually handles), so expanded into one row per known
    extraction format here — matching the same per-format shape the
    frontend already consumes from GET /api/marketplace/tools, so these
    slot into the same picker list with no special-casing."""
    from app.core import extraction_engines
    candidates = extraction_engines.list_mcp_candidates()
    return {
        "engines": [
            {"format": fmt, "engine_id": c["engine_id"], "name": c["name"], "description": c["description"], "default": False}
            for fmt in extraction_engines.ENGINES.keys()
            for c in candidates
        ]
    }


@router.get("/mcp-chunking-candidates")
def list_mcp_chunking_candidates():
    """Every connected MCP tool that plausibly chunks text — offered as a
    Chunk node strategy choice alongside the built-in strategies
    (app.core.chunking_engines.list_mcp_candidates)."""
    from app.core import chunking_engines
    return {"strategies": chunking_engines.list_mcp_candidates()}


@router.get("/mcp-embedding-candidates")
def list_mcp_embedding_candidates():
    """Every connected MCP tool that plausibly embeds text into a vector —
    offered as an Embedding node model choice alongside Marketplace-
    installed local models (app.core.embeddings.registry.list_mcp_candidates)."""
    from app.core.embeddings import registry as embeddings_registry
    return {"models": embeddings_registry.list_mcp_candidates()}


@router.get("")
def list_workflows(db: Session = Depends(get_db)):
    rows = db.query(Workflow).order_by(Workflow.updated_at.desc()).all()
    return {"workflows": [_serialize(w) for w in rows]}


@router.get("/{workflow_id}")
def get_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return _serialize(w)


@router.post("")
def create_workflow(payload: WorkflowPayload, db: Session = Depends(get_db)):
    w = Workflow(name=payload.name, graph_json=json.dumps(payload.graph))
    db.add(w)
    db.commit()
    db.refresh(w)
    return _serialize(w)


@router.put("/{workflow_id}")
def update_workflow(workflow_id: int, payload: WorkflowPayload, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    if w.seed_key:
        # Same "a seeded row stays intact" principle as delete_workflow's
        # own seed_key check below — a fresh install ships with this exact
        # node set and seed.py's self-healing (SEED_VERSION bumps, a
        # separate code path that writes graph_json directly, not through
        # this endpoint) expects to find and update it in place. Editing a
        # node's own settings, rewiring edges, or adding new nodes is still
        # fine — only removing one of the original nodes is blocked.
        current_ids = {n.get("id") for n in json.loads(w.graph_json).get("nodes", [])}
        incoming_ids = {n.get("id") for n in payload.graph.get("nodes", [])}
        if not current_ids.issubset(incoming_ids):
            raise HTTPException(
                status_code=400,
                detail=f"'{w.name}' is a default workflow — its steps can be reconfigured but not deleted.",
            )

    new_graph_json = json.dumps(payload.graph)
    if new_graph_json != w.graph_json:
        # Snapshot the OUTGOING graph, not the incoming one — a version
        # entry means "what this workflow looked like before this save",
        # so restoring it undoes the save that's about to happen. A no-op
        # save (canvas re-serialized to the same JSON) skips this — it
        # would just be a duplicate entry with nothing to restore to.
        _snapshot_version(db, workflow_id, w.name, w.graph_json)

    w.name = payload.name
    w.graph_json = new_graph_json
    db.commit()
    db.refresh(w)
    return _serialize(w)


def _snapshot_version(db: Session, workflow_id: int, name: str, graph_json: str) -> None:
    db.add(WorkflowVersion(workflow_id=workflow_id, name=name, graph_json=graph_json))
    db.flush()
    stale_ids = [
        v.id for v in db.query(WorkflowVersion.id)
        .filter(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.created_at.desc())
        .offset(WORKFLOW_VERSION_LIMIT)
    ]
    if stale_ids:
        db.query(WorkflowVersion).filter(WorkflowVersion.id.in_(stale_ids)).delete(synchronize_session=False)


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    if w.seed_key:
        # Any seeded row (currently just the one default pipeline) stays
        # undeletable — it's what a fresh install ships with and what
        # seed.py's own self-healing (SEED_VERSION bumps) expects to find
        # and update in place, not recreate from scratch after a user
        # deletes it by mistake.
        raise HTTPException(status_code=400, detail=f"'{w.name}' is a default workflow and can't be deleted.")
    # No ORM relationship/cascade wires these to Workflow — clean them up by
    # hand so deleting a workflow doesn't leave orphaned run/version rows
    # (harmless to SQLite, since it isn't enforcing the FK either way, but
    # pointless dead weight in these tables otherwise).
    db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow_id).delete(synchronize_session=False)
    db.query(WorkflowVersion).filter(WorkflowVersion.workflow_id == workflow_id).delete(synchronize_session=False)
    db.delete(w)
    db.commit()
    return {"message": f"Deleted workflow {workflow_id}."}


@router.post("/{workflow_id}/run")
def run(workflow_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Kicks off the run in the background and returns immediately — progress
    arrives over the WebSocket as workflow_node_progress/_complete/_failed
    events (see engine.run_workflow), the same live-progress pattern already
    used for MCP-connect and model-download progress."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    graph = json.loads(w.graph_json)
    if not graph.get("nodes"):
        raise HTTPException(status_code=400, detail="This workflow has no nodes yet.")

    run_id = uuid.uuid4().hex[:8]
    background_tasks.add_task(run_workflow, workflow_id, run_id, graph)
    return {"status": "running", "run_id": run_id}


@router.get("/{workflow_id}/runs")
def list_runs(workflow_id: int, db: Session = Depends(get_db)):
    """Recent run history — status/timing only, no per-node outputs (those
    can be large; fetch a specific run via GET .../runs/{run_id} for that).
    Written by app.core.workflows.engine's three run entrypoints so a run's
    outcome survives past the WebSocket messages that reported it live."""
    rows = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(50)
        .all()
    )
    return {"runs": [_serialize_run(r, include_outputs=False) for r in rows]}


@router.get("/{workflow_id}/runs/{run_id}")
def get_run(workflow_id: int, run_id: str, db: Session = Depends(get_db)):
    r = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow_id, WorkflowRun.id == run_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Run not found.")
    return _serialize_run(r, include_outputs=True)


def _serialize_run(r: WorkflowRun, include_outputs: bool) -> Dict:
    out = {
        "id": r.id,
        "workflow_id": r.workflow_id,
        "trigger": r.trigger,
        "status": r.status,
        "failed_node_id": r.failed_node_id,
        "error_message": r.error_message,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    if include_outputs:
        out["node_outputs"] = json.loads(r.node_outputs_json) if r.node_outputs_json else {}
    return out


@router.get("/{workflow_id}/versions")
def list_versions(workflow_id: int, db: Session = Depends(get_db)):
    """Snapshots taken automatically on every /PUT save that actually
    changed the graph (see update_workflow's _snapshot_version call) —
    graph_json is omitted here (can be large); fetch a specific version via
    GET .../versions/{version_id} for that, or restore it directly."""
    rows = (
        db.query(WorkflowVersion)
        .filter(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.created_at.desc())
        .all()
    )
    return {"versions": [{"id": v.id, "name": v.name, "created_at": v.created_at.isoformat() if v.created_at else None} for v in rows]}


@router.get("/{workflow_id}/versions/{version_id}")
def get_version(workflow_id: int, version_id: int, db: Session = Depends(get_db)):
    v = db.query(WorkflowVersion).filter(WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found.")
    return {"id": v.id, "name": v.name, "graph": json.loads(v.graph_json), "created_at": v.created_at.isoformat() if v.created_at else None}


@router.post("/{workflow_id}/versions/{version_id}/restore")
def restore_version(workflow_id: int, version_id: int, db: Session = Depends(get_db)):
    """Restores a workflow's graph to an earlier snapshot — itself
    snapshotted first (the CURRENT graph, right before being overwritten),
    same as any other save, so restoring is undoable too."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    v = db.query(WorkflowVersion).filter(WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found.")

    if v.graph_json != w.graph_json:
        _snapshot_version(db, workflow_id, w.name, w.graph_json)
    w.graph_json = v.graph_json
    db.commit()
    db.refresh(w)
    return _serialize(w)


def _validate_chat_handler_graph(graph: Dict) -> None:
    """A workflow can only be connected as the live chat handler if it has
    exactly one entry point (chat_trigger), exactly one Chat Reply node
    with exactly one "llm" node wired directly into it (and nothing else
    wired after that llm node) — model config lives only on "llm" nodes,
    "chat_reply" is a thin "send" marker with none of its own; see
    app.core.workflows.engine.run_chat_workflow's generation_ids detection,
    mirrored here — and exactly one final step — all counted only within
    the subgraph reachable from that trigger (via
    app.core.workflows.engine._reachable_subgraph), not the whole canvas,
    since one workflow may also hold an unrelated "On document upload"
    tree side by side. Raises HTTPException(400) with a message naming the
    exact problem rather than silently picking one node."""
    all_nodes = {n["id"]: n for n in graph.get("nodes", [])}
    all_edges = graph.get("edges", [])

    triggers = [n for n in all_nodes.values() if n.get("data", {}).get("kind") == "chat_trigger"]
    if len(triggers) == 0:
        raise HTTPException(status_code=400, detail="Add an \"On chat message\" trigger node before connecting this workflow to chat.")
    if len(triggers) > 1:
        raise HTTPException(status_code=400, detail="Only one \"On chat message\" trigger node is allowed per workflow.")

    nodes, edges = _reachable_subgraph(all_nodes, all_edges, triggers[0]["id"])

    reply_nodes = [n for n in nodes.values() if n.get("data", {}).get("kind") == "chat_reply"]
    if len(reply_nodes) == 0:
        raise HTTPException(status_code=400, detail="Add a Chat Reply node — it sends the reply.")
    if len(reply_nodes) > 1:
        raise HTTPException(status_code=400, detail="Only one Chat Reply node is allowed per chat-connected workflow.")

    generation_ids = [
        nid for nid in _upstream_node_ids(reply_nodes[0]["id"], edges)
        if nodes.get(nid, {}).get("data", {}).get("kind") == "llm"
        and _downstream_node_ids(nid, edges) == [reply_nodes[0]["id"]]
    ]
    if len(generation_ids) == 0:
        raise HTTPException(status_code=400, detail="Wire an \"llm\" node directly into the Chat Reply node — that's what generates the reply.")
    if len(generation_ids) > 1:
        raise HTTPException(status_code=400, detail="Only one \"llm\" node may lead ONLY into the Chat Reply node — that's the one treated as the reply generator.")

    downstream_of = {e["source"] for e in edges}
    terminal_nodes = [n for n in nodes.values() if n["id"] not in downstream_of]
    if len(terminal_nodes) == 0:
        raise HTTPException(status_code=400, detail="This workflow has no final step — every node leads to another node.")
    if len(terminal_nodes) > 1:
        raise HTTPException(status_code=400, detail="More than one node has nothing wired after it — there must be exactly one final step.")


def _trigger_conversation_id(graph: Dict, trigger_kind: str) -> Any:
    """Reads data.conversationId off the ONE trigger node of this kind —
    already validated to exist and be singular by the caller's own
    _validate_*_handler_graph. None/blank means "global" (every
    conversation with no more specific match), matching how this worked
    before per-conversation scoping existed."""
    for n in graph.get("nodes", []):
        if n.get("data", {}).get("kind") == trigger_kind:
            return n.get("data", {}).get("conversationId") or None
    return None


@router.post("/{workflow_id}/set-chat-handler")
def set_chat_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Connects this workflow as A live chat handler — either the one
    GLOBAL handler (its chat_trigger node's conversationId left blank) or
    scoped to one specific conversation (conversationId set on that node).
    Unseats only the previous holder of the SAME scope — a global handler
    and any number of differently-scoped handlers coexist; at most one
    workflow may hold any single scope (mirrors ModelRegistry's
    single-active-row pattern in app.api.context_config's load_active_model,
    just per-scope instead of globally). See app.db.crud.get_active_chat_workflow
    for the scoped-first-then-global lookup app.api.websocket's
    process_message_task reads at send time."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    graph = json.loads(w.graph_json)
    _validate_chat_handler_graph(graph)
    conversation_id = _trigger_conversation_id(graph, "chat_trigger")
    same_scope = (
        Workflow.chat_handler_conversation_id.is_(None) if conversation_id is None
        else Workflow.chat_handler_conversation_id == conversation_id
    )

    db.query(Workflow).filter(Workflow.id != workflow_id, same_scope).update(
        {Workflow.is_chat_handler: False, Workflow.chat_handler_conversation_id: None}, synchronize_session=False,
    )
    w.is_chat_handler = True
    w.chat_handler_conversation_id = conversation_id
    db.commit()
    return {"success": True, "conversation_id": conversation_id}


@router.post("/{workflow_id}/unset-chat-handler")
def unset_chat_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Disconnects this workflow — its scope (global or one conversation)
    goes back to having no chat handler at all, so a message there gets
    "⚠ No workflow is connected to handle chat..." (see
    app.api.websocket) until another workflow is connected. There's no
    built-in fallback pipeline to revert to anymore — chat is
    workflow-only."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    w.is_chat_handler = False
    w.chat_handler_conversation_id = None
    db.commit()
    return {"success": True}


def _validate_ingestion_handler_graph(graph: Dict) -> None:
    """A workflow can only be connected as the live ingestion handler if it
    has exactly one entry point (document_upload_trigger) and exactly one
    final step — both counted only within the subgraph reachable from that
    trigger (see _validate_chat_handler_graph's matching comment on why —
    the same workflow may also hold an unrelated "On chat message" tree).
    Mirrors _validate_chat_handler_graph's shape, minus the "exactly one
    LLM node" rule (ingestion has no reply to generate)."""
    all_nodes = {n["id"]: n for n in graph.get("nodes", [])}
    all_edges = graph.get("edges", [])

    triggers = [n for n in all_nodes.values() if n.get("data", {}).get("kind") == "document_upload_trigger"]
    if len(triggers) == 0:
        raise HTTPException(status_code=400, detail="Add an \"On document upload\" trigger node before connecting this workflow to ingestion.")
    if len(triggers) > 1:
        raise HTTPException(status_code=400, detail="Only one \"On document upload\" trigger node is allowed per workflow.")

    nodes, edges = _reachable_subgraph(all_nodes, all_edges, triggers[0]["id"])

    downstream_of = {e["source"] for e in edges}
    terminal_nodes = [n for n in nodes.values() if n["id"] not in downstream_of]
    if len(terminal_nodes) == 0:
        raise HTTPException(status_code=400, detail="This workflow has no final step — every node leads to another node.")
    if len(terminal_nodes) > 1:
        raise HTTPException(status_code=400, detail="More than one node has nothing wired after it — there must be exactly one final step.")


@router.post("/{workflow_id}/set-ingestion-handler")
def set_ingestion_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Connects this workflow as A live ingestion handler — either the one
    GLOBAL handler (its document_upload_trigger node's conversationId left
    blank) or scoped to uploads made within one conversation. Same
    unseat-only-the-same-scope rule as set_chat_handler above. See
    app.api.documents's upload handler and app.db.crud
    .get_active_ingestion_workflow for where/how this is read."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    graph = json.loads(w.graph_json)
    _validate_ingestion_handler_graph(graph)
    conversation_id = _trigger_conversation_id(graph, "document_upload_trigger")
    same_scope = (
        Workflow.ingestion_handler_conversation_id.is_(None) if conversation_id is None
        else Workflow.ingestion_handler_conversation_id == conversation_id
    )

    db.query(Workflow).filter(Workflow.id != workflow_id, same_scope).update(
        {Workflow.is_ingestion_handler: False, Workflow.ingestion_handler_conversation_id: None}, synchronize_session=False,
    )
    w.is_ingestion_handler = True
    w.ingestion_handler_conversation_id = conversation_id
    db.commit()
    return {"success": True, "conversation_id": conversation_id}


@router.post("/{workflow_id}/unset-ingestion-handler")
def unset_ingestion_handler(workflow_id: int, db: Session = Depends(get_db)):
    """Disconnects this workflow — its scope (global or one conversation)
    goes back to having no ingestion handler at all, so a document upload
    there is rejected (see app.api.documents's upload handler) until
    another workflow is connected. There's no built-in fallback pipeline
    to revert to anymore — ingestion is workflow-only."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    w.is_ingestion_handler = False
    w.ingestion_handler_conversation_id = None
    db.commit()
    return {"success": True}


def _serialize(w: Workflow) -> Dict:
    return {
        "id": w.id,
        "name": w.name,
        "graph": json.loads(w.graph_json),
        "is_chat_handler": bool(w.is_chat_handler),
        "is_ingestion_handler": bool(w.is_ingestion_handler),
        # Non-null only for a seeded row (currently just the one default
        # pipeline — see app.core.workflows.seed.SEED_WORKFLOW_KEY). Exposed
        # so the frontend can tell a seeded workflow apart from a
        # user-created one — e.g. to not offer to delete it.
        "seed_key": w.seed_key,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }
